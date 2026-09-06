"""LangGraph investigation workflow.

An explicit, bounded state machine (no uncontrolled agent loop). Each node is a
closure over the per-run ``Signals`` bundle, so state carries only declared data
channels. The single conditional branch is the sufficiency check.

    START -> load_context -> analyze_incident -> inspect_topology
          -> analyze_telemetry -> analyze_deployments
          -> retrieve_historical -> retrieve_runbooks
          -> generate_hypotheses -> evidence_validation -> contradiction_check
          -> rank -> sufficiency_check
                       ├── diagnosis_ready  -> diagnosis
                       └── insufficient     -> missing_information -> human_question
          -> generate_recommendation -> END
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.investigation.analysis import Signals
from app.investigation.correlation import evaluate_deployment_correlation
from app.investigation.deps import InvestigationDeps
from app.investigation.hypotheses import HypothesisGenerator
from app.investigation.models import (
    EvidenceSource,
    InvestigationState,
    InvestigationStatus,
)
from app.investigation.recommendation import build_recommendation
from app.investigation.scoring import (
    assess_sufficiency,
    rank_hypotheses,
    score_all,
)


def build_investigation_graph(
    signals: Signals,
    deps: InvestigationDeps,
    generator: HypothesisGenerator,
):
    """Compile a fresh graph bound to this run's signals."""

    def _audit(state, msg: str) -> list[str]:
        return [*state.get("audit", []), msg]

    def load_context(state: InvestigationState) -> dict:
        return {
            "observations": signals.observations,
            "evidence": signals.evidence,
            "topology_findings": signals.topology_findings,
            "audit": _audit(state, f"load_context: {len(signals.evidence)} evidence items"),
        }

    def analyze_incident(state: InvestigationState) -> dict:
        inc = signals.context.incident
        return {"audit": _audit(state, f"analyze_incident: {inc.id} on {inc.service}")}

    def inspect_topology(state: InvestigationState) -> dict:
        nb = signals.neighborhood
        return {
            "audit": _audit(
                state,
                f"inspect_topology: neighbourhood {nb.all} "
                f"(deps={nb.direct_deps}, dependents={nb.dependents})",
            )
        }

    def analyze_telemetry(state: InvestigationState) -> dict:
        findings = [
            e.finding for e in signals.evidence if e.source == EvidenceSource.TELEMETRY
        ]
        return {
            "telemetry_findings": findings,
            "audit": _audit(state, f"analyze_telemetry: {len(findings)} readings"),
        }

    def analyze_deployments(state: InvestigationState) -> dict:
        findings = [
            e.finding for e in signals.evidence if e.source == EvidenceSource.DEPLOYMENT
        ]
        return {
            "deployment_findings": findings,
            "audit": _audit(state, f"analyze_deployments: {len(findings)} recent deployment(s)"),
        }

    def retrieve_historical(state: InvestigationState) -> dict:
        hist = [e for e in signals.evidence if e.source == EvidenceSource.HISTORICAL]
        return {
            "historical_matches": hist,
            "audit": _audit(state, f"retrieve_historical: {len(hist)} match(es)"),
        }

    def retrieve_runbooks(state: InvestigationState) -> dict:
        rb = [e for e in signals.evidence if e.source == EvidenceSource.RUNBOOK]
        return {
            "runbook_findings": rb,
            "audit": _audit(state, f"retrieve_runbooks: {len(rb)} runbook(s)"),
        }

    def generate_hypotheses(state: InvestigationState) -> dict:
        hyps = generator.generate(signals)
        return {
            "hypotheses": hyps,
            "audit": _audit(state, f"generate_hypotheses: {len(hyps)} candidate(s) [{generator.name}]"),
        }

    def evidence_validation(state: InvestigationState) -> dict:
        scored = score_all(state["hypotheses"], signals)
        return {
            "hypotheses": scored,
            "audit": _audit(state, "evidence_validation: scored candidates against evidence"),
        }

    def contradiction_check(state: InvestigationState) -> dict:
        contradictions = [
            f"{h.id} ({h.kind}) contradicted by {h.evidence_against}"
            for h in state["hypotheses"]
            if h.evidence_against
        ]
        return {
            "contradictions": contradictions,
            "audit": _audit(state, f"contradiction_check: {len(contradictions)} contradiction(s)"),
        }

    def rank(state: InvestigationState) -> dict:
        ranked = rank_hypotheses(state["hypotheses"])
        top = ", ".join(f"{h.id}={h.confidence_score}" for h in ranked[:3])
        return {
            "ranked_hypotheses": ranked,
            "audit": _audit(state, f"rank: {top}"),
        }

    def sufficiency_check(state: InvestigationState) -> dict:
        ranked = state["ranked_hypotheses"]
        status, missing, questions = assess_sufficiency(ranked, signals, deps)
        primary = ranked[0] if ranked else None
        return {
            "investigation_status": status,
            "missing_information": missing,
            "human_questions": questions,
            "confidence": primary.confidence_score if primary else 0.0,
            "diagnosis": primary.statement if (primary and status == InvestigationStatus.DIAGNOSIS_READY) else None,
            "audit": _audit(state, f"sufficiency_check: {status.value}"),
        }

    def route(state: InvestigationState) -> str:
        return (
            "diagnosis"
            if state["investigation_status"] == InvestigationStatus.DIAGNOSIS_READY
            else "missing_information"
        )

    def diagnosis(state: InvestigationState) -> dict:
        primary = state["ranked_hypotheses"][0]
        return {"diagnosis": primary.statement, "audit": _audit(state, f"diagnosis: {primary.statement}")}

    def missing_information(state: InvestigationState) -> dict:
        return {
            "audit": _audit(
                state, f"missing_information: {len(state.get('missing_information', []))} gap(s)"
            )
        }

    def human_question(state: InvestigationState) -> dict:
        return {
            "audit": _audit(
                state, f"human_question: prepared {len(state.get('human_questions', []))} question(s)"
            )
        }

    def generate_recommendation(state: InvestigationState) -> dict:
        ranked = state["ranked_hypotheses"]
        primary = ranked[0] if ranked else None
        sufficient = state["investigation_status"] == InvestigationStatus.DIAGNOSIS_READY
        correlation = evaluate_deployment_correlation(signals, primary)
        rec = build_recommendation(primary, signals, sufficient=sufficient)
        return {
            "recommendation": rec,
            "deployment_correlation": correlation,
            "audit": _audit(
                state,
                f"generate_recommendation: correlation={correlation.level.value}; action='{rec.action}'",
            ),
        }

    g = StateGraph(InvestigationState)
    for name, fn in [
        ("load_context", load_context),
        ("analyze_incident", analyze_incident),
        ("inspect_topology", inspect_topology),
        ("analyze_telemetry", analyze_telemetry),
        ("analyze_deployments", analyze_deployments),
        ("retrieve_historical", retrieve_historical),
        ("retrieve_runbooks", retrieve_runbooks),
        ("generate_hypotheses", generate_hypotheses),
        ("evidence_validation", evidence_validation),
        ("contradiction_check", contradiction_check),
        ("rank", rank),
        ("sufficiency_check", sufficiency_check),
        ("diagnosis", diagnosis),
        ("missing_information", missing_information),
        ("human_question", human_question),
        ("generate_recommendation", generate_recommendation),
    ]:
        g.add_node(name, fn)

    g.add_edge(START, "load_context")
    g.add_edge("load_context", "analyze_incident")
    g.add_edge("analyze_incident", "inspect_topology")
    g.add_edge("inspect_topology", "analyze_telemetry")
    g.add_edge("analyze_telemetry", "analyze_deployments")
    g.add_edge("analyze_deployments", "retrieve_historical")
    g.add_edge("retrieve_historical", "retrieve_runbooks")
    g.add_edge("retrieve_runbooks", "generate_hypotheses")
    g.add_edge("generate_hypotheses", "evidence_validation")
    g.add_edge("evidence_validation", "contradiction_check")
    g.add_edge("contradiction_check", "rank")
    g.add_edge("rank", "sufficiency_check")
    g.add_conditional_edges(
        "sufficiency_check",
        route,
        {"diagnosis": "diagnosis", "missing_information": "missing_information"},
    )
    g.add_edge("diagnosis", "generate_recommendation")
    g.add_edge("missing_information", "human_question")
    g.add_edge("human_question", "generate_recommendation")
    g.add_edge("generate_recommendation", END)
    return g.compile()
