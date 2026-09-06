"""The default MockActionExecutor — safe, deterministic, never touches real infra.

It plays out a realistic execution lifecycle and, crucially, simulates the
resulting world state as a post-action telemetry snapshot that the verification
engine then reads. The simulation is deterministic and separate from the verdict:
the executor decides what the world looks like after the action; the verification
engine independently decides whether that world counts as "recovered". This is
why verification can genuinely FAIL (the ``no_recovery`` path) and trigger rollback
— it is not a rubber stamp.

The recovery simulation is a function of the action's verification plan (it
nominalises exactly the metrics the plan checks). It does NOT read any hidden
ground-truth label.
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timedelta

from app.domain.enums import HealthStatus
from app.domain.models import TelemetrySnapshot
from app.logging_config import get_logger
from app.remediation.executor import ActionExecutor, ExecutionRequest
from app.remediation.models import (
    ActionProposal,
    ExecutionResult,
    ExecutionStatus,
    MetricDirection,
    VerificationPlan,
)

logger = get_logger(__name__)


def _baseline_snapshot(service: str) -> TelemetrySnapshot:
    return TelemetrySnapshot(
        service=service,
        timestamp=datetime.utcnow(),
        error_rate=8.5,
        latency_ms=2500,
        throughput=100,
        health_status=HealthStatus.CRITICAL,
        active_connections=200,
        metadata={},
    )


def simulate_recovered(
    before: TelemetrySnapshot | None, service: str, plan: VerificationPlan | None
) -> TelemetrySnapshot:
    """A deterministic 'healthy' post-action snapshot that satisfies ``plan``."""
    base = deepcopy(before) if before is not None else _baseline_snapshot(service)
    after = TelemetrySnapshot(
        service=service,
        timestamp=(base.timestamp or datetime.utcnow()) + timedelta(minutes=5),
        error_rate=0.3,
        latency_ms=min(base.latency_ms or 120, 120) if base.latency_ms else 90,
        throughput=base.throughput or 100,
        health_status=HealthStatus.HEALTHY,
        active_connections=max(1, int((base.active_connections or 100) * 0.4)),
        metadata=dict(base.metadata or {}),
    )
    # Nominalise exactly the metadata metrics the plan will check.
    if plan is not None:
        for exp in plan.metrics:
            if exp.source != "metadata":
                continue
            if exp.direction == MetricDirection.BELOW and exp.threshold is not None:
                after.metadata[exp.metric] = round(exp.threshold * 0.5, 2)
            elif exp.direction == MetricDirection.ABOVE and exp.threshold is not None:
                after.metadata[exp.metric] = round(exp.threshold * 1.5, 2)
            elif exp.direction == MetricDirection.DECREASE:
                prior = (base.metadata or {}).get(exp.metric)
                after.metadata[exp.metric] = 0.0 if prior is None else round(float(_num(prior)) * 0.1, 2)
            else:
                after.metadata[exp.metric] = 0.0
    return after


def simulate_unchanged(
    before: TelemetrySnapshot | None, service: str
) -> TelemetrySnapshot:
    """Post-action snapshot for the failure path: the world did NOT recover."""
    if before is not None:
        snap = deepcopy(before)
        snap.timestamp = (before.timestamp or datetime.utcnow()) + timedelta(minutes=5)
        # Keep it clearly unhealthy so verification fails.
        if snap.health_status not in (HealthStatus.DEGRADED, HealthStatus.CRITICAL):
            snap.health_status = HealthStatus.CRITICAL
            snap.error_rate = max(snap.error_rate, 8.0)
        return snap
    return _baseline_snapshot(service)


def _num(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        import re

        m = re.search(r"-?\d+(?:\.\d+)?", str(value))
        return float(m.group(0)) if m else 0.0


class MockActionExecutor(ActionExecutor):
    @property
    def mode(self) -> str:
        return "mock"

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        proposal: ActionProposal = request.proposal
        exec_id = uuid.uuid4().hex
        started = datetime.utcnow()
        lifecycle: list[str] = [
            f"Action accepted: {proposal.action_id} on {proposal.target_service}"
            + (f" ({proposal.target_resource})" if proposal.target_resource else ""),
        ]

        problems = self.validate(request)
        if problems:
            lifecycle.append("Pre-flight validation FAILED: " + "; ".join(problems))
            return ExecutionResult(
                execution_id=exec_id,
                action_ref=request.action_ref or request.idempotency_key,
                idempotency_key=request.idempotency_key,
                mode=self.mode,
                status=ExecutionStatus.SKIPPED,
                attempt=request.attempt,
                lifecycle=lifecycle,
                message="Execution skipped: preconditions/authorisation not satisfied.",
                started_at=started,
                completed_at=datetime.utcnow(),
            )

        lifecycle.append("Preconditions validated")
        lifecycle.append("Approval/authorisation verified")
        lifecycle.append(
            "Action dispatched to MOCK executor "
            "(EXECUTION_MODE=mock — no real infrastructure is touched)"
        )

        window = (
            request.proposal.verification_plan.observation_window_seconds
            if request.proposal.verification_plan
            else 300
        )

        # ---- forced non-happy paths (tests / rollback demo) -----------------
        if request.outcome_override == "execution_failed":
            lifecycle.append(f"Mock executor reported FAILURE applying {proposal.action_id}")
            return self._finish(
                exec_id, request, ExecutionStatus.FAILED, lifecycle, started,
                message="The action failed during execution.", after=None,
            )
        if request.outcome_override == "timeout":
            lifecycle.append(f"Mock executor timed out after {request.action.timeout_seconds}s")
            return self._finish(
                exec_id, request, ExecutionStatus.TIMED_OUT, lifecycle, started,
                message="The action timed out.", after=None,
            )

        # ---- happy path: the action runs -----------------------------------
        lifecycle.append(f"Mock executor applied {proposal.action_id} to {proposal.target_service}")
        lifecycle.append(f"Waiting {window}s for stabilisation (simulated)")

        if request.outcome_override == "no_recovery":
            after = simulate_unchanged(request.before, proposal.target_service)
            lifecycle.append("Telemetry captured post-action: target did NOT recover")
        else:
            after = simulate_recovered(
                request.before, proposal.target_service, proposal.verification_plan
            )
            lifecycle.append(
                f"Telemetry captured post-action: error_rate={after.error_rate}%, "
                f"health={after.health_status.value}"
            )

        logger.info(
            "mock_execute: action=%s target=%s status=succeeded (mock, no real mutation)",
            proposal.action_id,
            proposal.target_service,
        )
        return self._finish(
            exec_id, request, ExecutionStatus.SUCCEEDED, lifecycle, started,
            message="Mock execution completed.", after=after,
        )

    def rollback(
        self, request: ExecutionRequest, execution: ExecutionResult
    ) -> ExecutionResult:
        events = [
            f"Rollback started for {request.proposal.action_id} on {request.proposal.target_service}",
        ]
        if not request.action.rollback_supported:
            events.append("Rollback NOT supported for this action — manual remediation required")
            execution.rollback_lifecycle = events
            execution.rolled_back = False
            return execution
        rb_id = request.proposal.rollback_action_id or request.action.action_id
        events.append(f"Applying rollback action {rb_id} (mock — no real mutation)")
        events.append("System restored to the pre-action state")
        events.append("Rollback completed")
        execution.rollback_lifecycle = events
        execution.rolled_back = True
        logger.info(
            "mock_rollback: action=%s target=%s rolled_back=True (mock)",
            request.proposal.action_id,
            request.proposal.target_service,
        )
        return execution

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _finish(
        exec_id: str,
        request: ExecutionRequest,
        status: ExecutionStatus,
        lifecycle: list[str],
        started: datetime,
        *,
        message: str,
        after: TelemetrySnapshot | None,
    ) -> ExecutionResult:
        before = request.before
        return ExecutionResult(
            execution_id=exec_id,
            action_ref=request.action_ref or request.idempotency_key,
            idempotency_key=request.idempotency_key,
            mode="mock",
            status=status,
            attempt=request.attempt,
            lifecycle=lifecycle,
            message=message,
            before=before.model_dump(mode="json") if before else None,
            after=after.model_dump(mode="json") if after else None,
            started_at=started,
            completed_at=datetime.utcnow(),
        )
