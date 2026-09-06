"""Signal extraction + evidence collection (deterministic, offline).

Turns the Phase 1 ``IncidentContext`` (plus topology-aware neighbourhood probes)
into a ``Signals`` bundle: current/baseline telemetry per neighbouring service,
recent deployments across the neighbourhood, historical + runbook evidence, and
a fully id'd ``InvestigationEvidence`` list. Both the hypothesis generator and
the deterministic scorer consume this bundle, so reasoning is reproducible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta

from app.domain.enums import HealthStatus
from app.domain.models import Deployment, IncidentContext, TelemetrySnapshot
from app.investigation.deps import InvestigationDeps
from app.investigation.models import (
    EvidenceKind,
    EvidenceSource,
    InvestigationEvidence,
    TopologyFinding,
)

# --- hypothesis kinds ------------------------------------------------------
KIND_DEPLOYMENT = "deployment_regression"
KIND_DB = "db_exhaustion"
KIND_REDIS = "redis_degradation"
KIND_KAFKA = "kafka_messaging"
KIND_PAYMENT = "payment_dependency"
KIND_DEPENDENCY = "dependency_degradation"
KIND_FRONTEND = "frontend_regression"
KIND_DATA = "data_integrity"
KIND_NETWORK = "network_infrastructure"
KIND_NOVEL = "novel_unknown"

# --- symptom keyword signatures (lexical hints, never the sole determinant) --
KEYWORDS: dict[str, list[str]] = {
    KIND_DB: [
        "too many clients", "connection pool", "db connection", "database",
        "pool exhaust", "max connections", "connection errors", "replication lag",
        "postgres", "connection reset by peer",
    ],
    KIND_REDIS: ["redis", "eviction", "evicted", "cache", "carts empty", "logged out"],
    KIND_KAFKA: [
        "kafka", "consumer", "partition", "packing slip", "warehouse", "not appearing",
        "missing from", "no new orders", "offset", "consumer lag", "data mismatch", "publish",
    ],
    KIND_PAYMENT: [
        "payment", "gateway", "deadline exceeded", "upstream provider", "503",
        "charge", "stripe", "adyen", "circuit breaker", "payment attempts",
    ],
    KIND_FRONTEND: [
        "blank", "white screen", "js error", "javascript", "render", "product page",
        "css", "console shows", "browser",
    ],
    KIND_DATA: [
        "negative", "fraud", "corrupt", "data mismatch", "wrong total", "discount",
        "incorrect", "integrity", "bad data", "final total",
    ],
    KIND_NETWORK: [
        "nat", "dns", "tls", "certificate", "load balancer", "packet loss",
        "az", "ip limits", "network",
    ],
}

# fingerprints of historical incidents -> hypothesis kind
FINGERPRINT_KIND = {
    "DB_Exhaustion": KIND_DB,
    "Deployment_Regression": KIND_DEPLOYMENT,
    "Redis_Issue": KIND_REDIS,
    "Kafka_Issue": KIND_KAFKA,
    "Payment_Timeout": KIND_PAYMENT,
    "Frontend_Error": KIND_FRONTEND,
    "PG_WMS_Issue": KIND_DB,
    "WMS_Sync": KIND_KAFKA,
}

_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)")


def _pct(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = _PCT_RE.search(str(value))
    return float(m.group(1)) if m else None


@dataclass
class Neighborhood:
    affected: str
    direct_deps: list[str]
    dependents: list[str]

    @property
    def all(self) -> list[str]:
        seen: list[str] = []
        for s in [self.affected, *self.direct_deps, *self.dependents]:
            if s not in seen:
                seen.append(s)
        return seen


@dataclass
class Signals:
    context: IncidentContext
    neighborhood: Neighborhood
    text: str
    telemetry_current: dict[str, TelemetrySnapshot]  # fresh-only (stale dropped)
    telemetry_baseline: dict[str, TelemetrySnapshot]
    deployments: list[Deployment]
    evidence: list[InvestigationEvidence]
    topology_findings: list[TopologyFinding]
    observations: list[str]
    stale_services: list[str] = field(default_factory=list)

    # -- convenience queries ----------------------------------------------
    def health(self, service: str) -> HealthStatus | None:
        snap = self.telemetry_current.get(service)
        return snap.health_status if snap else None

    def is_unhealthy(self, service: str) -> bool:
        return self.health(service) in (HealthStatus.DEGRADED, HealthStatus.CRITICAL)

    def is_healthy(self, service: str) -> bool:
        return self.health(service) == HealthStatus.HEALTHY

    def keyword_hits(self, kind: str) -> list[str]:
        return [kw for kw in KEYWORDS.get(kind, []) if kw in self.text]

    def evidence_by_id(self, eid: str) -> InvestigationEvidence | None:
        return next((e for e in self.evidence if e.id == eid), None)


class _EvidenceIdGen:
    def __init__(self) -> None:
        self._n = 0

    def next(self) -> str:
        self._n += 1
        return f"E{self._n}"


def _error_delta(current: TelemetrySnapshot, baseline: TelemetrySnapshot | None) -> str:
    if baseline is None:
        return f"error_rate={current.error_rate}%, latency={current.latency_ms}ms"
    return (
        f"error_rate {baseline.error_rate}% -> {current.error_rate}%, "
        f"latency {baseline.latency_ms}ms -> {current.latency_ms}ms"
    )


def build_signals(
    context: IncidentContext,
    deps: InvestigationDeps,
    human_facts: list[str] | None = None,
) -> Signals:
    """Collect all deterministic signals + id'd evidence for an incident."""
    human_facts = human_facts or []
    incident = context.incident
    at = incident.created_at

    topo = deps.topology_service
    affected = incident.service
    direct = [e.target_service for e in topo.direct_dependencies(affected)]
    dependents = [e.source_service for e in topo.direct_dependents(affected)]
    neighborhood = Neighborhood(affected=affected, direct_deps=direct, dependents=dependents)

    text = " ".join(
        [incident.title, incident.description, " ".join(incident.symptoms), *human_facts]
    ).lower()

    ids = _EvidenceIdGen()
    evidence: list[InvestigationEvidence] = []
    observations: list[str] = []

    # -- incident evidence (fact) -----------------------------------------
    evidence.append(
        InvestigationEvidence(
            id=ids.next(),
            source=EvidenceSource.INCIDENT,
            kind=EvidenceKind.FACT,
            service=affected,
            finding=f"Reported incident on {affected}: {incident.title}",
            strength=0.5,
            refs={"incident_id": incident.id, "symptoms": incident.symptoms},
            timestamp=incident.created_at,
        )
    )
    observations.append(f"Incident '{incident.title}' reported against {affected}.")

    # -- telemetry (current + baseline), topology-aware neighbourhood ------
    telemetry_current: dict[str, TelemetrySnapshot] = {}
    telemetry_baseline: dict[str, TelemetrySnapshot] = {}
    stale: list[str] = []
    relevance = timedelta(minutes=deps.telemetry_relevance_minutes)
    for svc in neighborhood.all:
        # Degrade gracefully: a raising telemetry source (e.g. New Relic down)
        # must not crash the investigation — treat that service as unknown.
        try:
            snap = deps.telemetry_source.get_service_health(svc, at)
        except Exception:  # noqa: BLE001
            stale.append(svc)
            continue
        if snap is None:
            continue
        # staleness guard: a reading far from the incident time is not a current fact
        fresh = (
            at is None
            or snap.timestamp is None
            or abs((snap.timestamp - at).total_seconds()) <= relevance.total_seconds()
        )
        baseline = None
        if at is not None:
            try:
                baseline = deps.telemetry_source.get_service_health(svc, at - timedelta(days=1))
            except Exception:  # noqa: BLE001
                baseline = None
            if baseline is not None:
                telemetry_baseline[svc] = baseline
        if not fresh:
            stale.append(svc)
            continue
        telemetry_current[svc] = snap
        if snap.health_status in (HealthStatus.DEGRADED, HealthStatus.CRITICAL):
            evidence.append(
                InvestigationEvidence(
                    id=ids.next(),
                    source=EvidenceSource.TELEMETRY,
                    kind=EvidenceKind.FACT,
                    service=svc,
                    finding=f"{svc} telemetry {snap.health_status.value}: "
                    f"{_error_delta(snap, baseline)}",
                    strength=0.9 if snap.health_status == HealthStatus.CRITICAL else 0.6,
                    refs={"health": snap.health_status.value, "metadata": snap.metadata},
                    timestamp=snap.timestamp,
                )
            )
            observations.append(
                f"{svc} is {snap.health_status.value} (err={snap.error_rate}%, "
                f"lat={snap.latency_ms}ms)."
            )
        else:
            evidence.append(
                InvestigationEvidence(
                    id=ids.next(),
                    source=EvidenceSource.TELEMETRY,
                    kind=EvidenceKind.FACT,
                    service=svc,
                    finding=f"{svc} telemetry healthy ({_error_delta(snap, baseline)})",
                    strength=0.5,
                    refs={"health": snap.health_status.value, "metadata": snap.metadata},
                    timestamp=snap.timestamp,
                )
            )

    # -- deployments: CONSUME the Phase 1 topology-aware deployment context --
    # (Phase 1 already scans the affected service + its dependencies. Phase 2
    #  evaluates these — it does not re-retrieve or infer a second source.)
    deployments: list[Deployment] = list(context.recent_deployments)
    deployments.sort(key=lambda d: (d.deployed_at or _min_dt(), d.id), reverse=True)
    for dep in deployments:
        mins = _minutes_before(dep, at)
        evidence.append(
            InvestigationEvidence(
                id=ids.next(),
                source=EvidenceSource.DEPLOYMENT,
                kind=EvidenceKind.FACT,
                service=dep.service,
                finding=(
                    f"Deployment {dep.id} ({dep.version}) on {dep.service} "
                    f"changed {dep.changed_components}; "
                    f"{'%d min before incident' % mins if mins is not None else 'timing unknown'}; "
                    f"status={dep.status.value}"
                ),
                strength=0.8,
                refs={
                    "deployment_id": dep.id,
                    "service": dep.service,
                    "minutes_before": mins,
                    "changed_components": dep.changed_components,
                    "status": dep.status.value,
                },
                timestamp=dep.deployed_at,
            )
        )
        observations.append(
            f"Deployment {dep.id} on {dep.service} "
            f"{'%d min before incident.' % mins if mins is not None else '(timing unknown).'}"
        )

    # -- historical evidence (inference) ----------------------------------
    for hist in context.similar_incidents:
        m = hist.metadata
        evidence.append(
            InvestigationEvidence(
                id=ids.next(),
                source=EvidenceSource.HISTORICAL,
                kind=EvidenceKind.INFERENCE,
                service=m.get("service"),
                finding=(
                    f"Historical {m.get('incident_id')} [{m.get('incident_fingerprint')}]: "
                    f"root cause was '{m.get('root_cause')}' (resolved: {m.get('resolution')})"
                ),
                strength=min(0.6, float(m.get("score") or 0.2) + 0.3),
                refs={
                    "incident_id": m.get("incident_id"),
                    "fingerprint": m.get("incident_fingerprint"),
                    "relevance": m.get("score"),
                    "service": m.get("service"),
                },
            )
        )

    # -- runbook evidence (inference) -------------------------------------
    for rb in context.relevant_runbooks:
        m = rb.metadata
        evidence.append(
            InvestigationEvidence(
                id=ids.next(),
                source=EvidenceSource.RUNBOOK,
                kind=EvidenceKind.INFERENCE,
                service=m.get("service") or None,
                finding=f"Runbook '{m.get('runbook_id')}' matches the incident symptoms",
                strength=min(0.5, float(m.get("score") or 0.2) + 0.2),
                refs={"runbook_id": m.get("runbook_id"), "relevance": m.get("score")},
            )
        )

    # -- human-supplied facts ---------------------------------------------
    for hf in human_facts:
        evidence.append(
            InvestigationEvidence(
                id=ids.next(),
                source=EvidenceSource.HUMAN,
                kind=EvidenceKind.FACT,
                finding=f"Operator-provided: {hf}",
                strength=0.8,
                refs={},
            )
        )
        observations.append(f"Operator provided additional information: {hf}")

    # -- topology findings -------------------------------------------------
    topo_findings: list[TopologyFinding] = []
    if direct:
        topo_findings.append(
            TopologyFinding(
                description=f"{affected} directly depends on: {', '.join(direct)}",
                services=[affected, *direct],
                relationship="depends_on",
            )
        )
    if dependents:
        topo_findings.append(
            TopologyFinding(
                description=f"{affected} is depended on by (upstream): {', '.join(dependents)}",
                services=[*dependents, affected],
                relationship="depended_on_by",
            )
        )

    return Signals(
        context=context,
        neighborhood=neighborhood,
        text=text,
        telemetry_current=telemetry_current,
        telemetry_baseline=telemetry_baseline,
        deployments=deployments,
        evidence=evidence,
        topology_findings=topo_findings,
        observations=observations,
        stale_services=stale,
    )


# --- metadata signal helpers ----------------------------------------------
def db_metadata_signal(snap: TelemetrySnapshot | None) -> tuple[bool, str]:
    if snap is None:
        return False, ""
    md = snap.metadata or {}
    conn = _pct(md.get("connection_usage_percent"))
    dbcpu = _pct(md.get("db_cpu_percent"))
    if conn is not None and conn >= 85:
        return True, f"connection_usage_percent={conn}"
    if dbcpu is not None and dbcpu >= 85:
        return True, f"db_cpu_percent={dbcpu}"
    if _pct(md.get("replication_lag_seconds")) and _pct(md.get("replication_lag_seconds")) >= 60:
        return True, f"replication_lag_seconds={md.get('replication_lag_seconds')}"
    return False, ""


def redis_metadata_signal(snap: TelemetrySnapshot | None) -> tuple[bool, str]:
    if snap is None:
        return False, ""
    md = snap.metadata or {}
    mem = _pct(md.get("memory_utilization"))
    if mem is not None and mem >= 90:
        return True, f"memory_utilization={md.get('memory_utilization')}"
    if _pct(md.get("oom_kills")) and _pct(md.get("oom_kills")) > 0:
        return True, f"oom_kills={md.get('oom_kills')}"
    return False, ""


def kafka_metadata_signal(snap: TelemetrySnapshot | None) -> tuple[bool, str]:
    if snap is None:
        return False, ""
    md = snap.metadata or {}
    lag = _pct(md.get("partition_lag"))
    if lag is not None and lag >= 1000:
        return True, f"partition_lag={lag}"
    if str(md.get("under_replicated")).lower() == "true":
        return True, "under_replicated=true"
    if str(md.get("consumer_state")).lower() == "offline":
        return True, "consumer_state=offline"
    return False, ""


def payment_metadata_signal(snap: TelemetrySnapshot | None) -> tuple[bool, str]:
    if snap is None:
        return False, ""
    md = snap.metadata or {}
    if _pct(md.get("upstream_timeouts")) and _pct(md.get("upstream_timeouts")) > 0:
        return True, f"upstream_timeouts={md.get('upstream_timeouts')}"
    if _pct(md.get("outbound_packet_drop")) and _pct(md.get("outbound_packet_drop")) > 0:
        return True, f"outbound_packet_drop={md.get('outbound_packet_drop')}"
    return False, ""


def data_metadata_signal(snap: TelemetrySnapshot | None) -> tuple[bool, str]:
    if snap is None:
        return False, ""
    md = snap.metadata or {}
    anomalies = _pct(md.get("anomalous_data_inserts"))
    if anomalies is not None and anomalies > 0:
        return True, f"anomalous_data_inserts={anomalies}"
    return False, ""


def frontend_metadata_signal(snap: TelemetrySnapshot | None) -> tuple[bool, str]:
    if snap is None:
        return False, ""
    md = snap.metadata or {}
    js = _pct(md.get("js_errors"))
    if js is not None and js >= 100:
        return True, f"js_errors={js}"
    return False, ""


def _minutes_before(dep: Deployment, at) -> int | None:
    if dep.deployed_at is None or at is None:
        return None
    return int((at - dep.deployed_at).total_seconds() // 60)


def _min_dt():
    from datetime import datetime

    return datetime.min
