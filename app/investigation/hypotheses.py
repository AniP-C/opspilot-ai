"""Hypothesis generation.

A ``HypothesisGenerator`` *proposes* candidate hypotheses (statement + kind +
implicated service). It never assigns final scores — the deterministic scorer
(``scoring.py``) evaluates evidence for/against and ranks them. This keeps LLM
generation (optional) cleanly separated from deterministic policy.

The default ``HeuristicHypothesisGenerator`` is fully offline and always
proposes *multiple* hypotheses, including a novel/unknown fallback, so the system
never collapses prematurely to a single answer.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.investigation.analysis import (
    KIND_DATA,
    KIND_DB,
    KIND_DEPENDENCY,
    KIND_DEPLOYMENT,
    KIND_FRONTEND,
    KIND_KAFKA,
    KIND_NETWORK,
    KIND_NOVEL,
    KIND_PAYMENT,
    KIND_REDIS,
    Signals,
    data_metadata_signal,
    db_metadata_signal,
    frontend_metadata_signal,
    kafka_metadata_signal,
    payment_metadata_signal,
    redis_metadata_signal,
)
from app.investigation.models import Hypothesis


@runtime_checkable
class HypothesisGenerator(Protocol):
    def generate(self, signals: Signals) -> list[Hypothesis]:
        ...

    @property
    def name(self) -> str:
        ...


class HeuristicHypothesisGenerator:
    """Deterministic, offline candidate generation from extracted signals."""

    @property
    def name(self) -> str:
        return "heuristic-v1"

    def generate(self, signals: Signals) -> list[Hypothesis]:
        nb = signals.neighborhood
        affected = nb.affected
        candidates: list[tuple[str, str, str | None, list[str]]] = []  # kind, stmt, svc, refs

        def svc_in(prefix: str) -> str | None:
            return next((s for s in nb.all if s.startswith(prefix)), None)

        db_dep = affected if affected.startswith("svc-pg") else svc_in("svc-pg")
        redis_dep = "svc-redis" if "svc-redis" in nb.all else None
        kafka_dep = "svc-kafka" if "svc-kafka" in nb.all else None
        payment_dep = "svc-payment" if "svc-payment" in nb.all else None

        # 1) Deployment regression — one per recent neighbourhood deployment.
        for dep in signals.deployments:
            candidates.append(
                (
                    KIND_DEPLOYMENT,
                    f"Deployment regression: {dep.service} {dep.version} "
                    f"(changed {', '.join(dep.changed_components) or 'unknown'})",
                    dep.service,
                    [f"deployment:{dep.id}"],
                )
            )

        # 2) Database exhaustion / degradation
        db_sig_svc = None
        for s in nb.all:
            ok, _ = db_metadata_signal(signals.telemetry_current.get(s))
            if ok:
                db_sig_svc = s
                break
        if db_dep and (
            (db_dep and signals.is_unhealthy(db_dep))
            or db_sig_svc is not None
            or (signals.keyword_hits(KIND_DB) and signals.is_unhealthy(affected))
        ):
            implicated = db_dep or db_sig_svc or affected
            candidates.append(
                (KIND_DB, f"Database connection exhaustion / degradation on {implicated}", implicated, [])
            )

        # 3) Redis degradation
        if redis_dep and (
            signals.is_unhealthy(redis_dep)
            or redis_metadata_signal(signals.telemetry_current.get(redis_dep))[0]
            or (signals.keyword_hits(KIND_REDIS) and (signals.is_unhealthy(redis_dep) or affected == redis_dep))
        ):
            candidates.append((KIND_REDIS, f"Cache/Redis degradation on {redis_dep}", redis_dep, []))
        elif affected == "svc-redis" and (signals.is_unhealthy(affected) or signals.keyword_hits(KIND_REDIS)):
            candidates.append((KIND_REDIS, "Cache/Redis degradation on svc-redis", "svc-redis", []))

        # 4) Kafka / messaging pipeline
        kafka_unhealthy = kafka_dep and (
            signals.is_unhealthy(kafka_dep)
            or kafka_metadata_signal(signals.telemetry_current.get(kafka_dep))[0]
        )
        wms_broken = "svc-wms" in nb.all and signals.is_unhealthy("svc-wms")
        if kafka_dep and (kafka_unhealthy or wms_broken or signals.keyword_hits(KIND_KAFKA)):
            candidates.append(
                (KIND_KAFKA, f"Messaging/Kafka pipeline issue (via {kafka_dep})", kafka_dep, [])
            )
        elif affected == "svc-kafka" and (signals.is_unhealthy(affected) or signals.keyword_hits(KIND_KAFKA)):
            candidates.append((KIND_KAFKA, "Kafka broker/partition issue on svc-kafka", "svc-kafka", []))

        # 5) Payment dependency
        if payment_dep and (
            signals.is_unhealthy(payment_dep)
            or payment_metadata_signal(signals.telemetry_current.get(payment_dep))[0]
            or (signals.keyword_hits(KIND_PAYMENT) and (signals.is_unhealthy(payment_dep) or affected == payment_dep))
        ):
            candidates.append((KIND_PAYMENT, f"Payment dependency failure on {payment_dep}", payment_dep, []))
        elif affected == "svc-payment" and (signals.is_unhealthy(affected) or signals.keyword_hits(KIND_PAYMENT)):
            candidates.append((KIND_PAYMENT, "Payment gateway failure on svc-payment", "svc-payment", []))

        # 6) Frontend regression
        if affected == "svc-frontend" and (
            signals.is_unhealthy(affected)
            or frontend_metadata_signal(signals.telemetry_current.get(affected))[0]
            or signals.keyword_hits(KIND_FRONTEND)
        ):
            candidates.append((KIND_FRONTEND, "Frontend regression on svc-frontend", "svc-frontend", []))

        # 7) Data integrity / application logic defect
        if data_metadata_signal(signals.telemetry_current.get(affected))[0] or signals.keyword_hits(KIND_DATA):
            candidates.append(
                (KIND_DATA, f"Application logic / data-integrity defect in {affected}", affected, [])
            )

        # 8) Network / infrastructure
        if signals.keyword_hits(KIND_NETWORK) or payment_metadata_signal(
            signals.telemetry_current.get(affected)
        )[0]:
            candidates.append(
                (KIND_NETWORK, f"Network/infrastructure fault affecting {affected}", affected, [])
            )

        # 9) Generic dependency degradation (unhealthy direct deps not otherwise covered)
        covered_services = {svc for _, _, svc, _ in candidates}
        for dep_svc in nb.direct_deps:
            if signals.is_unhealthy(dep_svc) and dep_svc not in covered_services:
                candidates.append(
                    (KIND_DEPENDENCY, f"Dependency degradation: {dep_svc}", dep_svc, [])
                )

        # 10) Always include a novel/unknown fallback (prevents false certainty).
        candidates.append(
            (KIND_NOVEL, f"Novel / undetermined failure affecting {affected}", affected, [])
        )

        # de-dupe by (kind, service) preserving first occurrence order
        seen: set[tuple[str, str | None]] = set()
        hypotheses: list[Hypothesis] = []
        idx = 0
        for kind, statement, service, refs in candidates:
            key = (kind, service)
            if key in seen:
                continue
            seen.add(key)
            idx += 1
            hypotheses.append(
                Hypothesis(
                    id=f"H{idx}",
                    kind=kind,
                    statement=statement,
                    implicated_service=service,
                    source_references=refs,
                )
            )
        return hypotheses


def get_hypothesis_generator(settings) -> HypothesisGenerator:
    """Select the generator. Heuristic by default; LLM only when explicitly on."""
    if getattr(settings, "investigation_llm_enabled", False):
        try:
            from app.investigation.llm import LLMHypothesisGenerator

            return LLMHypothesisGenerator(settings, fallback=HeuristicHypothesisGenerator())
        except Exception:  # noqa: BLE001 - never let LLM wiring break the engine
            return HeuristicHypothesisGenerator()
    return HeuristicHypothesisGenerator()
