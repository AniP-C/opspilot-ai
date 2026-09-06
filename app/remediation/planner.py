"""Action planner — proposes ONE structured, catalog-bound action (or nothing).

The planner is where a diagnosis becomes a *candidate* remediation. It is
deliberately constrained:

* It may only select an ``action_id`` that exists in the catalog, targeting a
  service the catalog allow-lists for that action. It cannot invent commands or
  targets.
* It proposes nothing (``None``) when the investigation is not diagnosis-ready,
  when the diagnosed pattern has no safe catalogued remediation (novel / network /
  data-flow-dependency), or when the chosen target is not allow-listed. "Nothing"
  routes to ESCALATE downstream — the safe default.
* A rollback is only proposed when Phase 2 actually *correlated* a specific recent
  deployment (HIGH/MEDIUM). This is the DEMO-008 guard: a recent-but-uncorrelated
  deployment (inventory) must never trigger a rollback.

Whether the proposed action is *allowed* is NOT decided here — that is the
deterministic policy engine's job. The planner only assembles the request.
"""

from __future__ import annotations

from app.investigation.analysis import (
    KIND_DATA,
    KIND_DB,
    KIND_DEPLOYMENT,
    KIND_FRONTEND,
    KIND_KAFKA,
    KIND_PAYMENT,
    KIND_REDIS,
)
from app.investigation.models import (
    CorrelationLevel,
    InvestigationResult,
    InvestigationStatus,
)
from app.remediation.catalog import ActionDefinition, get_action
from app.remediation.history import HistoricalStats
from app.remediation.models import ActionProposal


class ActionPlanner:
    def __init__(self, historical: HistoricalStats) -> None:
        self._hist = historical

    # -- public API --------------------------------------------------------
    def plan(
        self,
        result: InvestigationResult,
        *,
        affected_service: str,
        verification_window_seconds: int | None = None,
    ) -> ActionProposal | None:
        # Gate 1: only diagnosis-ready investigations get a concrete proposal.
        if result.status != InvestigationStatus.DIAGNOSIS_READY:
            return None
        primary = result.hypotheses[0] if result.hypotheses else None
        if primary is None:
            return None

        plan = self._select(primary.kind, affected_service, primary.implicated_service, result)
        if plan is None:
            return None
        action_id, target_service, target_resource, params, expected = plan

        action = get_action(action_id)
        if action is None or not action.allows_target(target_service):
            # Target not allow-listed for this action -> decline (safe default).
            return None

        # Observable precedent for this pattern on the affected service.
        precedent = self._hist.precedent(primary.kind, affected_service)

        rationale = (
            f"Diagnosis: {result.primary_hypothesis}. "
            f"Proposed {action.name.lower()} on {target_service}"
            + (f" ({target_resource})" if target_resource else "")
            + "."
        )
        return ActionProposal(
            action_id=action.action_id,
            name=action.name,
            target_service=target_service,
            target_resource=target_resource,
            parameters=params,
            rationale=rationale,
            evidence_ids=list(primary.evidence_for),
            confidence=result.confidence_score,
            expected_effect=expected,
            risk_level=action.risk_level,
            blast_radius=action.blast_radius,
            rollback_supported=action.rollback_supported,
            rollback_action_id=action.rollback_action_id,
            verification_plan=action.build_verification_plan(
                target_service, window_seconds=verification_window_seconds
            ),
            recurrence_count=precedent.recurrence_count,
            historical_success=precedent.successes,
            historical_total=precedent.total,
        )

    # -- kind -> (action, target) mapping ---------------------------------
    def _select(
        self,
        kind: str,
        affected: str,
        implicated: str | None,
        result: InvestigationResult,
    ) -> tuple[str, str, str | None, dict, str] | None:
        """Return (action_id, target_service, target_resource, params, expected_effect)."""
        if kind == KIND_DB:
            # Relieve connection-pool exhaustion by restarting the APP service
            # (never the database itself). Target = the affected app service.
            return (
                "restart_service",
                affected,
                implicated,  # the DB under pressure, for context
                {"strategy": "rolling"},
                "Stuck DB connections are dropped; connection utilisation and error rate normalise.",
            )

        if kind == KIND_DEPLOYMENT:
            return self._rollback_from_correlation(result)

        if kind == KIND_REDIS:
            return (
                "clear_cache",
                "svc-redis",
                None,
                {"mode": "adjust_eviction"},
                "Cache memory pressure is relieved; eviction/OOM stops and error rate normalises.",
            )

        if kind == KIND_KAFKA:
            # Restart the stalled consumer (WMS consumes the order stream).
            return (
                "restart_consumer",
                "svc-wms",
                "orders-consumer-group",
                {"consumer_group": "orders-consumer-group"},
                "Consumer resumes processing; partition lag drains and downstream sync recovers.",
            )

        if kind == KIND_PAYMENT:
            return (
                "failover_payment",
                "svc-payment",
                None,
                {"mode": "circuit_breaker_failover"},
                "Traffic fails over to the backup provider; payment error rate recovers.",
            )

        if kind == KIND_FRONTEND:
            # A frontend regression is a rollback IF a deploy is correlated;
            # otherwise a plain restart of the (stateless) frontend.
            rb = self._rollback_from_correlation(result)
            if rb is not None:
                return rb
            return (
                "restart_service",
                "svc-frontend",
                None,
                {"strategy": "rolling"},
                "Frontend workers restart; render/JS error rate recovers.",
            )

        if kind == KIND_DATA:
            return (
                "freeze_writes",
                affected,
                implicated,
                {"scope": "suspect_write_path"},
                "Writes on the suspect data path are halted to stop further corruption.",
            )

        # dependency_degradation / network_infrastructure / novel_unknown:
        # no safe catalogued action -> decline (routes to ESCALATE).
        return None

    def _rollback_from_correlation(
        self, result: InvestigationResult
    ) -> tuple[str, str, str | None, dict, str] | None:
        """Propose a rollback ONLY when a specific deployment is actually
        correlated (HIGH/MEDIUM). Guards against the DEMO-008 false correlation."""
        corr = result.deployment_correlation
        if corr is None or corr.level not in (CorrelationLevel.HIGH, CorrelationLevel.MEDIUM):
            return None
        if not corr.service or not corr.deployment_id:
            return None
        return (
            "rollback_deployment",
            corr.service,
            corr.deployment_id,
            {"deployment_id": corr.deployment_id},
            f"The regressing deployment {corr.deployment_id} is reverted to the previous good version.",
        )


def _action_is_real_enabled(action: ActionDefinition) -> bool:  # small helper for callers/tests
    return action.real_execution_enabled
