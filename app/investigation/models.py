"""Phase 2 domain models (Pydantic v2) and the LangGraph investigation state.

Design notes:
* Every conclusion is traceable to ``InvestigationEvidence`` (each with a stable
  ``id`` like ``E12``) and each evidence item is tagged FACT vs INFERENCE so the
  UI/consumer can clearly distinguish observed data from reasoning.
* We deliberately avoid claiming statistically calibrated probabilities. Scores
  are named ``support_score`` (raw evidence weight) and ``confidence_score``
  (a bounded 0..1 transform), not "probability".
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import IncidentContext


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class EvidenceKind(str, Enum):
    FACT = "fact"          # directly observed (telemetry reading, deployment record, incident field)
    INFERENCE = "inference"  # derived/suggested (historical similarity, runbook relevance)


class EvidenceSource(str, Enum):
    INCIDENT = "incident"
    TELEMETRY = "telemetry"          # New Relic
    TOPOLOGY = "topology"
    DEPLOYMENT = "deployment"
    HISTORICAL = "historical_incident"
    RUNBOOK = "runbook"
    HUMAN = "human"                  # operator-supplied additional info


class CorrelationLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class InvestigationStatus(str, Enum):
    DIAGNOSIS_READY = "diagnosis_ready"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    FAILED = "failed"


class InvestigationEvidence(_Base):
    id: str
    source: EvidenceSource
    kind: EvidenceKind
    service: str | None = None
    finding: str
    strength: float = 0.5  # 0..1 intrinsic weight of this evidence item
    refs: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime | None = None


class Hypothesis(_Base):
    id: str
    kind: str  # internal category, e.g. "deployment_regression", "db_exhaustion"
    statement: str
    implicated_service: str | None = None
    support_score: float = 0.0       # raw deterministic evidence weight
    confidence_score: float = 0.0    # bounded 0..1 transform (NOT a calibrated probability)
    evidence_for: list[str] = Field(default_factory=list)     # evidence ids
    evidence_against: list[str] = Field(default_factory=list)  # evidence ids
    source_references: list[str] = Field(default_factory=list)
    rationale: str = ""


class DeploymentCorrelation(_Base):
    """A deliberate judgement — recent deployment is evaluated, never assumed to
    be the cause."""

    deployment_id: str | None = None
    service: str | None = None
    level: CorrelationLevel = CorrelationLevel.NONE
    score: float = 0.0
    supporting: list[str] = Field(default_factory=list)
    contradicting: list[str] = Field(default_factory=list)
    rationale: str = ""


class TopologyFinding(_Base):
    description: str
    services: list[str] = Field(default_factory=list)
    relationship: str | None = None


class MissingInformationItem(_Base):
    topic: str
    detail: str
    why_needed: str = ""


class HumanQuestion(_Base):
    id: str
    question: str
    why: str = ""


class Recommendation(_Base):
    action: str
    rationale: str = ""
    risk_level: RiskLevel = RiskLevel.MEDIUM
    requires_human_approval: bool = True
    execution_available: bool = False  # always False in Phase 2
    source_runbook: str | None = None
    caveats: list[str] = Field(default_factory=list)


class InvestigationResult(_Base):
    incident_id: str
    investigation_id: str
    status: InvestigationStatus

    primary_hypothesis: str | None = None
    confidence_score: float = 0.0

    supporting_evidence: list[InvestigationEvidence] = Field(default_factory=list)
    contradicting_evidence: list[InvestigationEvidence] = Field(default_factory=list)
    alternative_hypotheses: list[Hypothesis] = Field(default_factory=list)

    deployment_correlation: DeploymentCorrelation | None = None
    topology_findings: list[TopologyFinding] = Field(default_factory=list)
    historical_findings: list[InvestigationEvidence] = Field(default_factory=list)

    recommended_next_action: str | None = None
    recommendation: Recommendation | None = None
    human_approval_required: bool = True  # advisory only — Phase 2 never executes

    missing_information: list[MissingInformationItem] = Field(default_factory=list)
    human_questions: list[HumanQuestion] = Field(default_factory=list)

    # Full working sets (for transparency / traceability).
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    evidence: list[InvestigationEvidence] = Field(default_factory=list)

    model_used: str = "heuristic-v1"
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    audit: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# LangGraph working state
# --------------------------------------------------------------------------- #
class InvestigationState(TypedDict, total=False):
    """Explicit, bounded graph state. Values are domain models where it matters.

    The graph is linear (plus one conditional branch), so each key is written by
    exactly one node — no reducers required.
    """

    incident_id: str
    investigation_id: str
    incident_context: IncidentContext
    human_facts: list[str]

    observations: list[str]
    topology_findings: list[TopologyFinding]
    telemetry_findings: list[str]
    deployment_findings: list[str]
    historical_matches: list[InvestigationEvidence]
    runbook_findings: list[InvestigationEvidence]

    evidence: list[InvestigationEvidence]
    hypotheses: list[Hypothesis]
    ranked_hypotheses: list[Hypothesis]
    contradictions: list[str]
    deployment_correlation: DeploymentCorrelation

    investigation_status: InvestigationStatus
    diagnosis: str | None
    confidence: float
    missing_information: list[MissingInformationItem]
    human_questions: list[HumanQuestion]
    recommendation: Recommendation

    audit: list[str]
    model_used: str
