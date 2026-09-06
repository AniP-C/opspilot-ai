"""Deployment correlation — evaluated, never assumed.

A recent deployment is NOT treated as the cause just because it is recent. We
weigh temporal proximity, service match, changed component, telemetry change,
error signature and historical similarity against contradicting evidence, and
emit an explicit HIGH / MEDIUM / LOW / NONE judgement with reasons.
"""

from __future__ import annotations

from app.investigation.analysis import Signals
from app.investigation.models import (
    CorrelationLevel,
    DeploymentCorrelation,
    Hypothesis,
)


def _pick_deployment(signals: Signals):
    """Choose the deployment most relevant to correlate (affected service first,
    else the closest in time)."""
    if not signals.deployments:
        return None
    affected = signals.neighborhood.affected
    on_affected = [d for d in signals.deployments if d.service == affected]
    if on_affected:
        return on_affected[0]  # already sorted most-recent-first
    return signals.deployments[0]


def evaluate_deployment_correlation(
    signals: Signals, primary: Hypothesis | None
) -> DeploymentCorrelation:
    dep = _pick_deployment(signals)
    if dep is None:
        return DeploymentCorrelation(
            level=CorrelationLevel.NONE,
            rationale="No deployments found within the window for the incident neighbourhood.",
        )

    affected = signals.neighborhood.affected
    score = 0.0
    supporting: list[str] = []
    contradicting: list[str] = []

    # temporal proximity
    mins = None
    if dep.deployed_at is not None and signals.context.incident.created_at is not None:
        mins = int((signals.context.incident.created_at - dep.deployed_at).total_seconds() // 60)
    if mins is not None and mins >= 0:
        if mins <= 30:
            score += 1.5
        else:
            score += 1.0
        supporting.append(f"deployed {mins} min before the incident")

    # service match
    if dep.service == affected:
        score += 2.0
        supporting.append(f"deployed service {dep.service} is the affected service")
    else:
        score -= 1.0
        contradicting.append(
            f"deployed service {dep.service} is not the affected service ({affected})"
        )

    # changed component relevance
    if any(c and c != "maintenance" for c in (dep.changed_components or [])):
        score += 1.0
        supporting.append(f"changed components {dep.changed_components} are functional")

    # telemetry change on the deployed service
    health = signals.health(dep.service)
    if health is not None:
        if health.value in ("degraded", "critical"):
            score += 1.5
            supporting.append(f"{dep.service} telemetry is {health.value} after the deploy")
        else:
            score -= 1.5
            contradicting.append(f"{dep.service} telemetry is healthy after the deploy")

    # historical similarity (deployment regressions)
    if any(
        str(e.refs.get("fingerprint")) == "Deployment_Regression"
        for e in signals.evidence
        if e.source.value == "historical_incident"
    ):
        score += 1.0
        supporting.append("historical deployment-regression incidents are similar")

    # error signature elsewhere
    if primary is not None and primary.implicated_service and primary.implicated_service != dep.service:
        score -= 1.0
        contradicting.append(
            f"failure signature points to {primary.implicated_service}, not {dep.service}"
        )

    if score >= 4.0:
        level = CorrelationLevel.HIGH
    elif score >= 2.0:
        level = CorrelationLevel.MEDIUM
    elif score >= 0.5:
        level = CorrelationLevel.LOW
    else:
        level = CorrelationLevel.NONE

    rationale = (
        f"{dep.id} on {dep.service}: correlation {level.value} (score {round(score, 2)}). "
        + ("Supporting: " + "; ".join(supporting) + ". " if supporting else "")
        + ("Contradicting: " + "; ".join(contradicting) + "." if contradicting else "")
    )
    return DeploymentCorrelation(
        deployment_id=dep.id,
        service=dep.service,
        level=level,
        score=round(score, 2),
        supporting=supporting,
        contradicting=contradicting,
        rationale=rationale,
    )
