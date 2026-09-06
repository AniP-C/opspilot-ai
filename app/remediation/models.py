"""Phase 3 domain models and controlled vocabularies (Pydantic v2).

These are the vendor-neutral models the remediation layer speaks. Nothing here
depends on ServiceNow / New Relic response shapes — adapters translate at the
boundary, exactly like Phase 1/2.

Design guarantees encoded in the types:
* An ``ActionProposal`` can only name an action that exists in the catalog and a
  target that is allow-listed for it (enforced by the planner/catalog, surfaced
  here as required fields + evidence references).
* A ``PolicyDecision`` is one of four explicit outcomes and always carries the
  reasons and the raw signals it was computed from — it is explainable by
  construction.
* Execution success (``ExecutionStatus``) is modelled separately from incident
  recovery (``VerificationStatus``) so the two are never conflated.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- #
# Controlled vocabularies
# --------------------------------------------------------------------------- #
class ActionRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class BlastRadius(str, Enum):
    """How far the effect of an action can plausibly reach."""

    SINGLE_SERVICE = "single_service"   # only the target service
    DEPENDENTS = "dependents"           # target + its direct upstream callers
    NEIGHBORHOOD = "neighborhood"       # target + dependencies + dependents
    GLOBAL = "global"                   # platform-wide


class PolicyDecision(str, Enum):
    AUTO_EXECUTE = "auto_execute"
    ASK_HUMAN = "ask_human"
    ESCALATE = "escalate"
    REJECT = "reject"


class ApprovalDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    INVESTIGATE = "investigate"   # human wants Phase 2 to gather more first


class ExecutionStatus(str, Enum):
    """Outcome of the *executor* — did the action run? (NOT whether it worked.)"""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    SKIPPED = "skipped"          # idempotency / not permitted


class VerificationStatus(str, Enum):
    """Did telemetry confirm the incident actually recovered?"""

    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"
    SKIPPED = "skipped"


class RemediationStatus(str, Enum):
    """Overall lifecycle state of a remediation action record (persisted)."""

    PROPOSED = "proposed"
    ESCALATED = "escalated"                     # policy/handler handed to a human (terminal)
    REJECTED_BY_POLICY = "rejected_by_policy"   # policy REJECT (terminal)
    AWAITING_APPROVAL = "awaiting_approval"     # policy ASK_HUMAN
    APPROVED = "approved"                       # human approved (ready to execute)
    REJECTED = "rejected"                       # human rejected (terminal)
    AUTO_APPROVED = "auto_approved"             # policy AUTO_EXECUTE (ready to execute)
    EXECUTING = "executing"
    EXECUTED = "executed"                       # executor succeeded (not yet verified)
    EXECUTION_FAILED = "execution_failed"
    VERIFYING = "verifying"
    VERIFIED = "verified"                       # verification PASSED -> incident recovered (terminal-success)
    VERIFICATION_FAILED = "verification_failed"
    ROLLED_BACK = "rolled_back"                 # verification/execution failed, rollback done (terminal)
    ROLLBACK_FAILED = "rollback_failed"         # terminal, needs human


class MetricDirection(str, Enum):
    DECREASE = "decrease"   # after < before
    INCREASE = "increase"   # after > before
    BELOW = "below"         # after <= threshold
    ABOVE = "above"         # after >= threshold


class AuditEventType(str, Enum):
    INVESTIGATION_COMPLETED = "INVESTIGATION_COMPLETED"
    ACTION_PROPOSED = "ACTION_PROPOSED"
    POLICY_EVALUATED = "POLICY_EVALUATED"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    ACTION_APPROVED = "ACTION_APPROVED"
    ACTION_REJECTED = "ACTION_REJECTED"
    ACTION_AUTO_APPROVED = "ACTION_AUTO_APPROVED"
    ACTION_STARTED = "ACTION_STARTED"
    ACTION_COMPLETED = "ACTION_COMPLETED"
    ACTION_FAILED = "ACTION_FAILED"
    VERIFICATION_STARTED = "VERIFICATION_STARTED"
    VERIFICATION_PASSED = "VERIFICATION_PASSED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    ROLLBACK_STARTED = "ROLLBACK_STARTED"
    ROLLBACK_COMPLETED = "ROLLBACK_COMPLETED"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"
    ESCALATED = "ESCALATED"
    SERVICENOW_UPDATED = "SERVICENOW_UPDATED"
    FEEDBACK_RECORDED = "FEEDBACK_RECORDED"


# --------------------------------------------------------------------------- #
# Verification plan (attached to each action; consumed after execution)
# --------------------------------------------------------------------------- #
class MetricExpectation(_Base):
    """One deterministic recovery criterion.

    ``source`` selects where to read the value: a top-level telemetry field
    (``error_rate``/``latency_ms``/...) or a key inside the snapshot ``metadata``.
    """

    metric: str
    direction: MetricDirection
    threshold: float | None = None
    source: str = "telemetry"   # telemetry | metadata
    description: str = ""


class VerificationPlan(_Base):
    target_service: str
    metrics: list[MetricExpectation] = Field(default_factory=list)
    require_health_recovered: bool = True
    observation_window_seconds: int = 300
    timeout_seconds: int = 600
    success_criteria: str = "All recovery criteria met and health no longer critical."


# --------------------------------------------------------------------------- #
# Action proposal (LLM/heuristic OUTPUT — a request, never a command)
# --------------------------------------------------------------------------- #
class ActionProposal(_Base):
    action_id: str                       # must exist in the action catalog
    name: str
    target_service: str                  # must be allow-listed for the action
    target_resource: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)

    rationale: str = ""
    evidence_ids: list[str] = Field(default_factory=list)   # Phase 2 evidence ids (E1, E2…)
    confidence: float = 0.0
    expected_effect: str = ""

    risk_level: ActionRisk = ActionRisk.MEDIUM
    blast_radius: BlastRadius = BlastRadius.SINGLE_SERVICE
    rollback_supported: bool = False
    rollback_action_id: str | None = None
    verification_plan: VerificationPlan | None = None

    # Derived precedent (observable history only — never ground truth):
    recurrence_count: int = 0
    historical_success: int = 0
    historical_total: int = 0

    @property
    def historical_success_rate(self) -> float | None:
        if self.historical_total <= 0:
            return None
        return round(self.historical_success / self.historical_total, 3)


# --------------------------------------------------------------------------- #
# Policy decision (deterministic engine OUTPUT — explainable by construction)
# --------------------------------------------------------------------------- #
class PolicySignals(_Base):
    """Every observable input the decision was computed from (for the audit).

    Action-specific fields are optional so the engine can also record a decision
    when there is NO proposal (the safe-default ESCALATE path)."""

    diagnosis_ready: bool
    confidence: float
    action_id: str | None = None
    action_risk: ActionRisk | None = None
    blast_radius: BlastRadius | None = None
    target_service: str | None = None
    target_criticality: str | None = None
    target_allow_listed: bool = False
    recurrence_count: int = 0
    historical_success_rate: float | None = None
    contradicting_evidence: bool = False
    reversible: bool = False
    verification_possible: bool = False
    environment: str = "production"


class PolicyResult(_Base):
    decision: PolicyDecision
    reasons: list[str] = Field(default_factory=list)
    signals: PolicySignals
    requires_human_approval: bool
    auto_execute_eligible: bool


# --------------------------------------------------------------------------- #
# Approval / execution / verification records
# --------------------------------------------------------------------------- #
class ApprovalRecord(_Base):
    action_ref: str
    decision: ApprovalDecision
    approver: str = "operator"
    note: str = ""
    decided_at: datetime | None = None


class ExecutionResult(_Base):
    execution_id: str
    action_ref: str
    idempotency_key: str
    mode: str                     # mock | real
    status: ExecutionStatus
    attempt: int = 1
    lifecycle: list[str] = Field(default_factory=list)   # human-readable event stream
    message: str = ""
    before: dict[str, Any] | None = None   # telemetry snapshot (serialised) at execution time
    after: dict[str, Any] | None = None    # simulated/observed post-action telemetry snapshot
    started_at: datetime | None = None
    completed_at: datetime | None = None
    rolled_back: bool = False
    rollback_lifecycle: list[str] = Field(default_factory=list)


class MetricObservation(_Base):
    metric: str
    before: float | str | None = None
    after: float | str | None = None
    expectation: str = ""
    met: bool = False


class VerificationResult(_Base):
    action_ref: str
    status: VerificationStatus
    observations: list[MetricObservation] = Field(default_factory=list)
    before_health: str | None = None
    after_health: str | None = None
    duration_seconds: int = 0
    reason: str = ""


class AuditEvent(_Base):
    incident_id: str
    action_ref: str | None = None
    event_type: AuditEventType
    summary: str
    detail: dict[str, Any] = Field(default_factory=dict)
    actor: str = "system"
    at: datetime | None = None


# --------------------------------------------------------------------------- #
# Aggregate view returned by the API / rendered by the UI
# --------------------------------------------------------------------------- #
class RemediationRecord(_Base):
    """The persisted remediation action + everything known about its lifecycle."""

    action_ref: str                 # stable id: incident_id + action_id (+ attempt)
    incident_id: str
    investigation_id: str | None = None
    status: RemediationStatus
    proposal: ActionProposal | None = None
    policy: PolicyResult | None = None
    approval: ApprovalRecord | None = None
    execution: ExecutionResult | None = None
    verification: VerificationResult | None = None
    servicenow_updated: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RemediationView(_Base):
    """Full Phase 3 payload for one incident: the decision + audit trail."""

    incident_id: str
    investigation_id: str | None = None
    diagnosis: str | None = None
    confidence: float = 0.0
    investigation_status: str | None = None
    record: RemediationRecord | None = None
    audit: list[AuditEvent] = Field(default_factory=list)
