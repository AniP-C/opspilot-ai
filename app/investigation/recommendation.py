"""Advisory recommendation builder.

Produces a *recommended next action* grounded in the primary hypothesis and, when
possible, the matched runbook's "Recommended Actions" section. This is advisory
only: ``execution_available`` is always False and ``requires_human_approval`` is
always True. Phase 2 never executes anything.
"""

from __future__ import annotations

import re

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
)
from app.investigation.models import (
    Hypothesis,
    Recommendation,
    RiskLevel,
)

_ACTION = {
    KIND_DEPLOYMENT: ("Evaluate rolling back the recently deployed version", RiskLevel.MEDIUM),
    KIND_DB: ("Scale DB connections / terminate long-running queries per runbook", RiskLevel.MEDIUM),
    KIND_REDIS: ("Adjust Redis eviction policy / scale cache memory", RiskLevel.MEDIUM),
    KIND_KAFKA: ("Restart the affected consumer and investigate the Kafka broker/partition", RiskLevel.MEDIUM),
    KIND_PAYMENT: ("Trip the payment circuit breaker / fail over to the backup provider", RiskLevel.MEDIUM),
    KIND_FRONTEND: ("Roll back the frontend deployment", RiskLevel.LOW),
    KIND_DEPENDENCY: ("Mitigate the degraded dependency per its runbook", RiskLevel.MEDIUM),
    KIND_DATA: ("Pause the affected flow and require human review before any data change", RiskLevel.HIGH),
    KIND_NETWORK: ("Engage network/infrastructure on-call (NAT/DNS/LB/AZ)", RiskLevel.MEDIUM),
    KIND_NOVEL: ("Escalate to a human incident commander — automated diagnosis is inconclusive", RiskLevel.MEDIUM),
}


def _runbook_actions(signals: Signals, kind: str) -> tuple[str | None, str | None]:
    """Return (runbook_id, recommended-actions text) from the top matching runbook."""
    for rb in signals.context.relevant_runbooks:
        content = rb.content or ""
        m = re.search(r"##\s*Recommended Actions\s*(.+?)(?:\n##\s|\Z)", content, re.S | re.I)
        if m:
            return rb.metadata.get("runbook_id"), " ".join(m.group(1).split())
    return None, None


def build_recommendation(
    primary: Hypothesis | None, signals: Signals, *, sufficient: bool
) -> Recommendation:
    if primary is None or not sufficient or primary.kind == KIND_NOVEL:
        return Recommendation(
            action="Escalate to a human incident commander; gather the missing information first",
            rationale="Evidence is insufficient for a confident automated diagnosis.",
            risk_level=RiskLevel.MEDIUM,
            requires_human_approval=True,
            execution_available=False,
            caveats=["Remediation will require Phase 3 risk/approval controls."],
        )

    action, risk = _ACTION.get(primary.kind, ("Escalate to a human incident commander", RiskLevel.MEDIUM))
    runbook_id, runbook_actions = _runbook_actions(signals, primary.kind)
    rationale = f"Primary hypothesis: {primary.statement} (confidence {primary.confidence_score})."
    if runbook_actions:
        rationale += f" Runbook '{runbook_id}' suggests: {runbook_actions}"

    caveats = ["Remediation will require Phase 3 risk/approval controls.", "Execution not available in Phase 2."]
    if primary.kind == KIND_DATA:
        caveats.insert(0, "HIGH SENSITIVITY: potential data-integrity/financial impact — human sign-off mandatory.")

    return Recommendation(
        action=action,
        rationale=rationale,
        risk_level=risk,
        requires_human_approval=True,
        execution_available=False,
        source_runbook=runbook_id,
        caveats=caveats,
    )
