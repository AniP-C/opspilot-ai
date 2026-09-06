"""InvestigationEngine — runs the LangGraph workflow and assembles the result.

It consumes a Phase 1 ``IncidentContext`` (built via the existing
``ContextBuilder``) and the Phase 1 collaborators (telemetry, topology,
deployments, retrievers) behind their interfaces — no subsystem is duplicated.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.context.builder import ContextBuilder
from app.domain.models import IncidentContext
from app.investigation.analysis import build_signals
from app.investigation.deps import InvestigationDeps
from app.investigation.graph import build_investigation_graph
from app.investigation.hypotheses import get_hypothesis_generator
from app.investigation.models import (
    EvidenceSource,
    InvestigationResult,
    InvestigationState,
    InvestigationStatus,
)
from app.logging_config import get_logger

logger = get_logger(__name__)


class InvestigationEngine:
    def __init__(
        self,
        deps: InvestigationDeps,
        generator,
        context_builder: ContextBuilder,
        model_used: str | None = None,
    ) -> None:
        self.deps = deps
        self.generator = generator
        self.context_builder = context_builder
        self.model_used = model_used or generator.name

    # -- public API --------------------------------------------------------
    def investigate(
        self,
        incident_id: str,
        *,
        investigation_id: str | None = None,
        human_facts: list[str] | None = None,
    ) -> InvestigationResult:
        # Reuse Phase 1 to build the context (raises IncidentNotFound / *Unavailable).
        context = self.context_builder.build(incident_id)
        return self.investigate_context(
            context, investigation_id=investigation_id, human_facts=human_facts
        )

    def investigate_context(
        self,
        context: IncidentContext,
        *,
        investigation_id: str | None = None,
        human_facts: list[str] | None = None,
    ) -> InvestigationResult:
        investigation_id = investigation_id or uuid.uuid4().hex
        human_facts = human_facts or []
        started = datetime.utcnow()
        logger.info(
            "investigation: start id=%s incident=%s human_facts=%d model=%s",
            investigation_id,
            context.incident.id,
            len(human_facts),
            self.model_used,
        )

        signals = build_signals(context, self.deps, human_facts)
        graph = build_investigation_graph(signals, self.deps, self.generator)

        init_state: InvestigationState = {
            "incident_id": context.incident.id,
            "investigation_id": investigation_id,
            "incident_context": context,
            "human_facts": human_facts,
            "audit": [],
            "model_used": self.model_used,
        }
        final = graph.invoke(init_state)
        result = self._assemble(final, signals, investigation_id, started, human_facts)
        logger.info(
            "investigation: done id=%s status=%s primary=%r confidence=%.2f "
            "correlation=%s duration_ms=%s",
            investigation_id,
            result.status.value,
            result.primary_hypothesis,
            result.confidence_score,
            result.deployment_correlation.level.value if result.deployment_correlation else "n/a",
            result.duration_ms,
        )
        return result

    # -- assembly ----------------------------------------------------------
    def _assemble(
        self,
        state: InvestigationState,
        signals,
        investigation_id: str,
        started: datetime,
        human_facts: list[str],
    ) -> InvestigationResult:
        ranked = state.get("ranked_hypotheses", [])
        primary = ranked[0] if ranked else None
        status: InvestigationStatus = state.get(
            "investigation_status", InvestigationStatus.INSUFFICIENT_EVIDENCE
        )

        supporting = (
            [e for eid in primary.evidence_for if (e := signals.evidence_by_id(eid))]
            if primary
            else []
        )
        contradicting = (
            [e for eid in primary.evidence_against if (e := signals.evidence_by_id(eid))]
            if primary
            else []
        )
        historical = [e for e in signals.evidence if e.source == EvidenceSource.HISTORICAL]
        alternatives = ranked[1 : self.deps.max_hypotheses] if len(ranked) > 1 else []

        rec = state.get("recommendation")
        completed = datetime.utcnow()
        return InvestigationResult(
            incident_id=state["incident_id"],
            investigation_id=investigation_id,
            status=status,
            primary_hypothesis=primary.statement if primary else None,
            confidence_score=primary.confidence_score if primary else 0.0,
            supporting_evidence=supporting,
            contradicting_evidence=contradicting,
            alternative_hypotheses=alternatives,
            deployment_correlation=state.get("deployment_correlation"),
            topology_findings=state.get("topology_findings", []),
            historical_findings=historical,
            recommended_next_action=rec.action if rec else None,
            recommendation=rec,
            human_approval_required=True,  # advisory; Phase 2 never executes
            missing_information=state.get("missing_information", []),
            human_questions=state.get("human_questions", []),
            hypotheses=ranked,
            evidence=signals.evidence,
            model_used=self.model_used,
            started_at=started,
            completed_at=completed,
            duration_ms=int((completed - started).total_seconds() * 1000),
            audit=state.get("audit", []),
        )


def make_investigation_engine(
    session: Session, settings: Settings | None = None
) -> InvestigationEngine:
    """Wire an engine that reuses the Phase 1 context builder + collaborators."""
    from app.api.dependencies import make_context_builder

    settings = settings or get_settings()
    builder = make_context_builder(session, settings)
    deps = InvestigationDeps(
        telemetry_source=builder.telemetry_source,
        topology_service=builder.topology_service,
        deployment_repo=builder.deployment_repo,
        historical_retriever=builder.historical_retriever,
        runbook_retriever=builder.runbook_retriever,
        deployment_window_minutes=settings.deployment_window_minutes,
        telemetry_relevance_minutes=settings.telemetry_relevance_minutes,
        max_hypotheses=settings.max_hypotheses,
        sufficiency_confidence=settings.sufficiency_confidence,
        sufficiency_margin=settings.sufficiency_margin,
    )
    generator = get_hypothesis_generator(settings)
    model_used = (
        settings.investigation_llm_model
        if settings.investigation_llm_enabled
        else generator.name
    )
    return InvestigationEngine(deps, generator, builder, model_used=model_used)
