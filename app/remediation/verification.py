"""Verification engine — did the incident *actually* recover?

Execution success (the executor ran) is NEVER treated as recovery. After every
action we re-read telemetry (through the same telemetry abstraction Phase 1 uses)
and evaluate the action's ``VerificationPlan`` deterministically against the
before/after snapshots. Only if every recovery criterion is met AND health is no
longer critical do we report PASSED.

This is deliberately dumb and explicit: each criterion is a (metric, direction,
threshold) triple with a recorded before value, after value and pass/fail — so the
verdict is fully auditable and can genuinely FAIL (which routes to rollback).
"""

from __future__ import annotations

import re

from app.domain.enums import HealthStatus
from app.domain.models import TelemetrySnapshot
from app.remediation.models import (
    MetricDirection,
    MetricExpectation,
    MetricObservation,
    VerificationPlan,
    VerificationResult,
    VerificationStatus,
)

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    m = _NUM_RE.search(str(value))
    return float(m.group(0)) if m else None


def _read(snap: TelemetrySnapshot | None, exp: MetricExpectation) -> float | None:
    if snap is None:
        return None
    if exp.source == "metadata":
        return _to_float((snap.metadata or {}).get(exp.metric))
    return _to_float(getattr(snap, exp.metric, None))


def _evaluate(exp: MetricExpectation, before: float | None, after: float | None) -> bool:
    # An optional metric absent from BOTH snapshots is "not applicable" -> satisfied.
    if before is None and after is None and exp.source == "metadata":
        return True
    if after is None:
        return False
    if exp.direction == MetricDirection.BELOW:
        return exp.threshold is not None and after <= exp.threshold
    if exp.direction == MetricDirection.ABOVE:
        return exp.threshold is not None and after >= exp.threshold
    if exp.direction == MetricDirection.DECREASE:
        return before is not None and after < before
    if exp.direction == MetricDirection.INCREASE:
        return before is not None and after > before
    return False


def _health_recovered(before: TelemetrySnapshot | None, after: TelemetrySnapshot | None) -> bool:
    if after is None:
        return False
    if after.health_status == HealthStatus.CRITICAL:
        return False
    if after.health_status == HealthStatus.HEALTHY:
        return True
    # after is DEGRADED/UNKNOWN: accept only if strictly better than a critical before.
    return before is not None and before.health_status == HealthStatus.CRITICAL


class VerificationEngine:
    def verify(
        self,
        plan: VerificationPlan,
        before: TelemetrySnapshot | None,
        after: TelemetrySnapshot | None,
        *,
        duration_seconds: int = 0,
    ) -> VerificationResult:
        action_ref = ""  # filled by the caller/service
        if after is None:
            return VerificationResult(
                action_ref=action_ref,
                status=VerificationStatus.INCONCLUSIVE,
                before_health=before.health_status.value if before else None,
                after_health=None,
                duration_seconds=duration_seconds,
                reason="No post-action telemetry was available to verify recovery.",
            )

        observations: list[MetricObservation] = []
        for exp in plan.metrics:
            b = _read(before, exp)
            a = _read(after, exp)
            met = _evaluate(exp, b, a)
            observations.append(
                MetricObservation(
                    metric=f"{exp.metric}{'(meta)' if exp.source == 'metadata' else ''}",
                    before=b,
                    after=a,
                    expectation=exp.description or f"{exp.metric} {exp.direction.value}"
                    + (f" {exp.threshold}" if exp.threshold is not None else ""),
                    met=met,
                )
            )

        metrics_ok = all(o.met for o in observations) if observations else True
        health_ok = (not plan.require_health_recovered) or _health_recovered(before, after)
        passed = metrics_ok and health_ok

        if passed:
            reason = "All recovery criteria met; health is no longer critical."
        elif not health_ok:
            reason = (
                f"Health did not recover (after={after.health_status.value})."
                if metrics_ok
                else "Recovery criteria not met and health did not recover."
            )
        else:
            failed = [o.metric for o in observations if not o.met]
            reason = f"Recovery criteria not met: {', '.join(failed)}."

        return VerificationResult(
            action_ref=action_ref,
            status=VerificationStatus.PASSED if passed else VerificationStatus.FAILED,
            observations=observations,
            before_health=before.health_status.value if before else None,
            after_health=after.health_status.value,
            duration_seconds=duration_seconds,
            reason=reason,
        )
