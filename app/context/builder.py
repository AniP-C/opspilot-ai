"""The Ops Context Engine orchestrator.

``ContextBuilder.build(incident_id)`` gathers all Phase 1 context sources and
returns a unified ``IncidentContext``. Design rules:

* The **incident** is the one mandatory input. If it can't be fetched we raise
  (``IncidentNotFound`` / ``IncidentSourceUnavailable``) — we never fabricate one.
* Every *other* source degrades gracefully: on failure we log it, record a
  ``MissingInfo`` entry, and continue. Context generation must not crash.
* The orchestration is **deterministic**: stable inputs produce stable output
  (fixed probe order, stable sorts, deterministic retrieval).
* Phase 1 only *gathers* context. It performs no diagnosis, correlation
  judgement or remediation.
"""

from __future__ import annotations

from app.db.repositories import (
    DeploymentRepository,
    EvidenceRepository,
    ServiceRepository,
)
from app.domain.interfaces import (
    HistoricalIncidentRetriever,
    IncidentSource,
    RunbookRetriever,
    TelemetrySource,
)
from app.domain.models import (
    Evidence,
    Incident,
    IncidentContext,
    MissingInfo,
    Service,
    ServiceTopology,
    TelemetrySnapshot,
)
from app.logging_config import get_logger
from app.topology.service import TopologyService

logger = get_logger(__name__)


class ContextBuilder:
    def __init__(
        self,
        *,
        incident_source: IncidentSource,
        service_repo: ServiceRepository,
        topology_service: TopologyService,
        telemetry_source: TelemetrySource,
        deployment_repo: DeploymentRepository,
        historical_retriever: HistoricalIncidentRetriever,
        runbook_retriever: RunbookRetriever,
        deployment_window_minutes: int = 60,
        deployment_scope: str = "dependencies",
        historical_top_k: int = 5,
        runbook_top_k: int = 3,
        evidence_repo: EvidenceRepository | None = None,
    ) -> None:
        self.incident_source = incident_source
        self.service_repo = service_repo
        self.topology_service = topology_service
        self.telemetry_source = telemetry_source
        self.deployment_repo = deployment_repo
        self.historical_retriever = historical_retriever
        self.runbook_retriever = runbook_retriever
        self.deployment_window_minutes = deployment_window_minutes
        self.deployment_scope = deployment_scope
        self.historical_top_k = historical_top_k
        self.runbook_top_k = runbook_top_k
        self.evidence_repo = evidence_repo

    # -- public API --------------------------------------------------------
    def build(self, incident_id: str, *, persist_evidence: bool = False) -> IncidentContext:
        logger.info("context_build: start incident_id=%s", incident_id)

        # 1) Incident — mandatory. Let IncidentNotFound / IncidentSourceUnavailable
        #    propagate to the caller (API maps them to 404 / 502).
        incident = self.incident_source.get_incident(incident_id)

        missing: list[MissingInfo] = []

        # 2) Affected service
        affected_service = self._lookup_service(incident, missing)

        # 3) Topology
        topology = self._build_topology(incident, missing)

        # 4) Telemetry (affected service + its direct dependencies)
        telemetry = self._gather_telemetry(incident, topology, missing)

        # 5) Recent deployments across the topology-relevant services
        #    (observed context only — no causation inference)
        recent_deployments = self._recent_deployments(incident, topology, missing)

        # 6) Similar historical incidents
        similar = self._historical(incident, missing)

        # 7) Relevant runbooks
        runbooks = self._runbooks(incident, missing)

        context = IncidentContext(
            incident=incident,
            affected_service=affected_service,
            dependency_path=topology,
            telemetry=telemetry,
            recent_deployments=recent_deployments,
            similar_incidents=similar,
            relevant_runbooks=runbooks,
            missing_information=missing,
        )

        if persist_evidence and self.evidence_repo is not None:
            try:
                self.evidence_repo.replace_for_incident(incident.id, similar + runbooks)
            except Exception as exc:  # noqa: BLE001 - persistence is best-effort
                logger.warning("context_build: evidence persistence failed: %s", exc)

        logger.info(
            "context_build: done incident_id=%s telemetry=%d deployments=%d "
            "similar=%d runbooks=%d missing=%d",
            incident.id,
            len(telemetry),
            len(recent_deployments),
            len(similar),
            len(runbooks),
            len(missing),
        )
        return context

    # -- steps -------------------------------------------------------------
    def _lookup_service(self, incident: Incident, missing: list[MissingInfo]) -> Service | None:
        try:
            service = self.service_repo.get_service(incident.service)
        except Exception as exc:  # noqa: BLE001
            logger.warning("topology_lookup: service lookup failed: %s", exc)
            missing.append(MissingInfo(source="service", detail=f"service lookup failed: {exc}"))
            return None
        if service is None:
            missing.append(
                MissingInfo(
                    source="service",
                    detail=f"service '{incident.service}' not found in topology store",
                )
            )
        return service

    def _build_topology(self, incident: Incident, missing: list[MissingInfo]) -> ServiceTopology:
        try:
            topology = self.topology_service.build_topology(incident.service)
            logger.info(
                "topology_lookup: service=%s direct_downstream=%d direct_upstream=%d "
                "dependency_chain=%d",
                incident.service,
                len(topology.direct_downstream),
                len(topology.direct_upstream),
                len(topology.dependency_chain),
            )
            return topology
        except Exception as exc:  # noqa: BLE001
            logger.warning("topology_lookup: failed: %s", exc)
            missing.append(MissingInfo(source="topology", detail=f"topology lookup failed: {exc}"))
            return ServiceTopology(service=incident.service)

    def _gather_telemetry(
        self,
        incident: Incident,
        topology: ServiceTopology,
        missing: list[MissingInfo],
    ) -> list[TelemetrySnapshot]:
        # Probe the affected service first, then its direct dependency targets
        # (deterministic order; dependencies already sorted by target id).
        probe_order: list[str] = [incident.service]
        for edge in topology.direct_dependencies:
            if edge.target_service not in probe_order:
                probe_order.append(edge.target_service)

        telemetry: list[TelemetrySnapshot] = []
        no_data: list[str] = []
        try:
            for svc in probe_order:
                snap = self.telemetry_source.get_service_health(svc, incident.created_at)
                if snap is None:
                    no_data.append(svc)
                else:
                    telemetry.append(snap)
        except Exception as exc:  # noqa: BLE001 - e.g. New Relic unreachable
            logger.warning("telemetry_lookup: source unavailable: %s", exc)
            missing.append(
                MissingInfo(source="telemetry", detail=f"telemetry source unavailable: {exc}")
            )
            return []

        if no_data:
            missing.append(
                MissingInfo(
                    source="telemetry",
                    detail=f"no telemetry available for: {', '.join(no_data)}",
                )
            )
        return telemetry

    def _deployment_scope_services(
        self, incident: Incident, topology: ServiceTopology
    ) -> list[str]:
        """Which services' deployments count as context, per configured scope.

        A dependency incident (e.g. an OMS problem actually caused by a change to
        a service OMS depends on) requires looking beyond the incident's declared
        service — otherwise the relevant deployment is silently missed.
        """
        scope = (self.deployment_scope or "dependencies").strip().lower()
        services = [incident.service]
        if scope in ("dependencies", "neighborhood"):
            services += topology.direct_downstream
        if scope == "neighborhood":
            services += topology.direct_upstream
        return list(dict.fromkeys(services))

    def _recent_deployments(
        self, incident: Incident, topology: ServiceTopology, missing: list[MissingInfo]
    ):
        if incident.created_at is None:
            missing.append(
                MissingInfo(
                    source="deployments",
                    detail="incident has no timestamp; cannot compute deployment window",
                )
            )
            return []
        services = self._deployment_scope_services(incident, topology)
        try:
            deployments = self.deployment_repo.get_recent_for_services(
                services, incident.created_at, self.deployment_window_minutes
            )
            logger.info(
                "deployment_retrieval: scope=%s services=%s window=%dm -> %d deployment(s)",
                self.deployment_scope,
                services,
                self.deployment_window_minutes,
                len(deployments),
            )
            return deployments
        except Exception as exc:  # noqa: BLE001
            logger.warning("deployment_retrieval: failed: %s", exc)
            missing.append(
                MissingInfo(source="deployments", detail=f"deployment retrieval failed: {exc}")
            )
            return []

    def _historical(self, incident: Incident, missing: list[MissingInfo]) -> list[Evidence]:
        query = f"{incident.title}. {incident.description}".strip()
        try:
            # Metadata-filtered retrieval by the affected service first.
            results = self.historical_retriever.search(
                query, where={"service": incident.service}, top_k=self.historical_top_k
            )
            # Fall back to an unfiltered search if the service has little history.
            if not results:
                results = self.historical_retriever.search(query, top_k=self.historical_top_k)
        except Exception as exc:  # noqa: BLE001
            logger.warning("retrieval: historical failed: %s", exc)
            missing.append(
                MissingInfo(source="historical_incidents", detail=f"retrieval failed: {exc}")
            )
            return []
        # Exclude the subject incident from its own similar set.
        results = [e for e in results if e.metadata.get("incident_id") != incident.id]
        if not results:
            missing.append(
                MissingInfo(
                    source="historical_incidents",
                    detail="no similar historical incidents found",
                )
            )
        return results

    def _runbooks(self, incident: Incident, missing: list[MissingInfo]) -> list[Evidence]:
        parts = [incident.title, incident.description, " ".join(incident.symptoms)]
        query = ". ".join(p for p in parts if p).strip()
        try:
            results = self.runbook_retriever.search(query, top_k=self.runbook_top_k)
        except Exception as exc:  # noqa: BLE001
            logger.warning("retrieval: runbooks failed: %s", exc)
            missing.append(MissingInfo(source="runbooks", detail=f"retrieval failed: {exc}"))
            return []
        if not results:
            missing.append(MissingInfo(source="runbooks", detail="no relevant runbooks found"))
        return results
