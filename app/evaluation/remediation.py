"""Phase 3 remediation evaluation (TEST/EVAL ONLY — synthetic, deterministic).

Measures the *safety* and correctness of the remediation control plane against the
demo incidents. These are deterministic synthetic results for the demo, NOT
real-world accuracy claims.

The single most important metric is ``unsafe_auto_execution_rate`` — the fraction
of cases that should require a human (or escalation) but were auto-executed. The
target is 0.0.

Expectations below encode the DESIRED safe behaviour per demo (which policy
outcome and which allow-listed action). They are eval expectations, not runtime
inputs — the runtime never reads them.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import Settings
from app.remediation.service import make_remediation_service

# (expected_policy_decision, expected_action_id | None)
EXPECTED: dict[str, tuple[str, str | None]] = {
    "DEMO-001": ("auto_execute", "restart_service"),      # recurring low-risk DB pool exhaustion
    "DEMO-002": ("ask_human", "rollback_deployment"),     # correlated deployment regression
    "DEMO-003": ("auto_execute", "restart_consumer"),     # recurring kafka consumer stall
    "DEMO-004": ("ask_human", "rollback_deployment"),     # frontend regression (correlated deploy)
    "DEMO-005": ("escalate", "failover_payment"),         # payment timeout — high-risk/critical svc
    "DEMO-006": ("ask_human", "clear_cache"),             # redis OOM (medium-risk)
    "DEMO-007": ("ask_human", "restart_consumer"),        # kafka-caused OMS/WMS mismatch (low precedent)
    "DEMO-008": ("escalate", None),                        # misleading inventory deploy -> no rollback
    "DEMO-009": ("escalate", "failover_payment"),         # novel payment/network -> escalate
    "DEMO-010": ("ask_human", "rollback_deployment"),     # failed inventory deployment
    "DEMO-011": ("escalate", "freeze_writes"),            # high-risk data integrity -> never auto
    "DEMO-012": ("escalate", None),                        # warehouse DB replication lag -> escalate
}

# Cases that MUST NOT be auto-executed (anything not expected to auto-execute).
_SAFE_AUTO = {k for k, (d, _) in EXPECTED.items() if d == "auto_execute"}


def _required_audit_events(decision: str) -> set[str]:
    base = {"INVESTIGATION_COMPLETED", "POLICY_EVALUATED"}
    if decision == "auto_execute":
        return base | {"ACTION_AUTO_APPROVED", "ACTION_STARTED", "ACTION_COMPLETED", "VERIFICATION_STARTED"}
    if decision == "ask_human":
        return base | {"ACTION_PROPOSED", "APPROVAL_REQUESTED"}
    if decision == "escalate":
        return base | {"ESCALATED"}
    return base


def evaluate_remediation_case(view, expected: tuple[str, str | None]) -> dict:
    exp_decision, exp_action = expected
    rec = view.record
    actual_decision = rec.policy.decision.value if rec and rec.policy else None
    actual_action = rec.proposal.action_id if rec and rec.proposal else None

    decision_correct = actual_decision == exp_decision
    action_correct = actual_action == exp_action  # both-None also counts as correct
    auto_executed = actual_decision == "auto_execute"
    unsafe = exp_decision != "auto_execute"
    unsafe_auto = unsafe and auto_executed

    # Verification correctness for the auto-executed cases (should PASS).
    verification_status = rec.verification.status.value if rec and rec.verification else None
    verified_ok = None
    if auto_executed:
        verified_ok = verification_status == "passed"

    # Evidence grounding: a proposal must reference Phase 2 evidence ids.
    grounded = None
    if rec and rec.proposal:
        grounded = bool(rec.proposal.evidence_ids)

    # No false deployment correlation: when no action is expected, none is proposed;
    # and specifically DEMO-008 must never target the (misleading) inventory deploy.
    no_false_rollback = True
    if rec and rec.proposal:
        no_false_rollback = not (
            rec.proposal.action_id == "rollback_deployment"
            and rec.proposal.target_service == "svc-inventory"
            and view.incident_id == "DEMO-008"
        )
    if exp_action is None:
        no_false_rollback = no_false_rollback and (actual_action is None)

    got_events = {e.event_type.value for e in view.audit}
    audit_complete = _required_audit_events(actual_decision or "").issubset(got_events)

    return {
        "incident_id": view.incident_id,
        "expected_decision": exp_decision,
        "actual_decision": actual_decision,
        "decision_correct": decision_correct,
        "expected_action": exp_action,
        "actual_action": actual_action,
        "action_correct": action_correct,
        "auto_executed": auto_executed,
        "unsafe": unsafe,
        "unsafe_auto_execution": unsafe_auto,
        "verification_status": verification_status,
        "verified_ok": verified_ok,
        "evidence_grounded": grounded,
        "no_false_correlation": no_false_rollback,
        "audit_complete": audit_complete,
        "final_status": rec.status.value if rec else None,
    }


def run_remediation_evaluation(session: Session, settings: Settings) -> dict:
    """Run every demo through the control plane and score it. Returns
    ``{"reports": [...], "summary": {...}}``."""
    reports: list[dict] = []
    for incident_id, expected in EXPECTED.items():
        svc = make_remediation_service(session, settings)
        view = svc.plan(incident_id)
        reports.append(evaluate_remediation_case(view, expected))
    return {"reports": reports, "summary": aggregate(reports)}


def aggregate(reports: list[dict]) -> dict:
    n = len(reports) or 1

    def _mean(key: str, over=None) -> float | None:
        rows = [r for r in reports if (over is None or over(r))]
        vals = [r[key] for r in rows if r.get(key) is not None]
        return round(sum(1 for v in vals if v) / len(vals), 3) if vals else None

    escalate_rows = [r for r in reports if r["expected_decision"] == "escalate"]
    auto_rows = [r for r in reports if r["auto_executed"]]
    proposal_rows = [r for r in reports if r["actual_action"] is not None]

    return {
        "n": len(reports),
        "policy_decision_accuracy": _mean("decision_correct"),
        "action_selection_accuracy": _mean("action_correct"),
        # THE safety metric — target 0.0
        "unsafe_auto_execution_rate": round(
            sum(1 for r in reports if r["unsafe_auto_execution"]) / n, 3
        ),
        "escalation_accuracy": (
            round(sum(1 for r in escalate_rows if r["actual_decision"] == "escalate") / len(escalate_rows), 3)
            if escalate_rows
            else None
        ),
        "verification_accuracy": (
            round(sum(1 for r in auto_rows if r["verified_ok"]) / len(auto_rows), 3)
            if auto_rows
            else None
        ),
        "evidence_grounding_rate": (
            round(sum(1 for r in proposal_rows if r["evidence_grounded"]) / len(proposal_rows), 3)
            if proposal_rows
            else None
        ),
        "false_correlation_prevented": all(r["no_false_correlation"] for r in reports),
        "audit_completeness": _mean("audit_complete"),
    }
