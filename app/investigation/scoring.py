"""Deterministic evidence scoring, ranking and sufficiency assessment.

Given candidate hypotheses + the ``Signals`` bundle, this attaches supporting and
contradicting evidence to each hypothesis and computes a raw ``support_score``
and a bounded ``confidence_score`` (never claimed to be a calibrated
probability). It then decides whether the evidence is *sufficient* for a
diagnosis or whether the investigation must ask for more information.

This layer is intentionally independent of how hypotheses were generated (heuristic
or LLM), so the policy is reproducible and testable.
"""

from __future__ import annotations

import math

from app.investigation.analysis import (
    FINGERPRINT_KIND,
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
    data_metadata_signal,
    db_metadata_signal,
    frontend_metadata_signal,
    kafka_metadata_signal,
    payment_metadata_signal,
    redis_metadata_signal,
)
from app.investigation.deps import InvestigationDeps
from app.investigation.models import (
    EvidenceKind,
    EvidenceSource,
    HumanQuestion,
    Hypothesis,
    InvestigationStatus,
    MissingInformationItem,
)

_ASSERTS_UNHEALTHY = {
    KIND_DB, KIND_REDIS, KIND_KAFKA, KIND_PAYMENT, KIND_DEPENDENCY,
    KIND_FRONTEND, KIND_NETWORK, KIND_DEPLOYMENT,
}
# kinds where a failing *caller* (the affected service) supports a dependency cause
_DEP_KINDS = {KIND_DB, KIND_KAFKA, KIND_REDIS, KIND_PAYMENT, KIND_DEPENDENCY, KIND_NETWORK}

_META_FN = {
    KIND_DB: db_metadata_signal,
    KIND_REDIS: redis_metadata_signal,
    KIND_KAFKA: kafka_metadata_signal,
    KIND_PAYMENT: payment_metadata_signal,
    KIND_DATA: data_metadata_signal,
    KIND_FRONTEND: frontend_metadata_signal,
    KIND_NETWORK: payment_metadata_signal,  # NAT/packet-drop shows up here
}
_META_WEIGHT = {KIND_DATA: 1.8, KIND_DB: 1.5}  # default 1.5

_RUNBOOK_HINTS = {
    KIND_DB: ("db-connection", "database"),
    KIND_REDIS: ("redis",),
    KIND_KAFKA: ("kafka", "warehouse-sync", "wms"),
    KIND_PAYMENT: ("payment",),
    KIND_FRONTEND: ("frontend",),
    KIND_DEPLOYMENT: ("deployment", "rollback"),
}


def _confidence(support: float) -> float:
    # Bounded transform; capped below 1.0 — we never claim absolute certainty.
    return max(0.02, min(0.97, 1.0 / (1.0 + math.exp(-(support - 2.2)))))


def _relevant_components(components: list) -> bool:
    return any(c and c != "maintenance" for c in (components or []))


def score_hypothesis(h: Hypothesis, signals: Signals) -> None:
    """Mutates ``h`` in place, setting scores + evidence links + rationale."""
    kind = h.kind
    implicated = h.implicated_service
    affected = signals.neighborhood.affected
    support = 0.0
    for_ids: list[str] = []
    against: list[str] = []
    reasons: list[str] = []

    if kind == KIND_NOVEL:
        return  # scored in a second pass (needs the field of specific hypotheses)

    # -- symptom keywords --------------------------------------------------
    kws = signals.keyword_hits(kind)
    if kws:
        support += min(len(kws) * 0.6, 1.5)
        for_ids.append("E1")  # incident evidence
        reasons.append(f"symptom keywords {kws[:3]}")

    # -- metadata signal ---------------------------------------------------
    meta_fn = _META_FN.get(kind)
    if meta_fn is not None:
        for probe in [implicated, affected]:
            snap = signals.telemetry_current.get(probe) if probe else None
            ok, detail = meta_fn(snap)
            if ok:
                support += _META_WEIGHT.get(kind, 1.5)
                reasons.append(f"telemetry metadata {detail} on {probe}")
                ev = next(
                    (e for e in signals.evidence
                     if e.source == EvidenceSource.TELEMETRY and e.service == probe),
                    None,
                )
                if ev:
                    for_ids.append(ev.id)
                break

    # -- telemetry per service --------------------------------------------
    for e in signals.evidence:
        if e.source != EvidenceSource.TELEMETRY:
            continue
        healthy = str(e.refs.get("health")) == "healthy"
        svc = e.service
        if kind == KIND_DEPLOYMENT:
            if svc == implicated:
                if healthy:
                    support -= 1.8
                    against.append(e.id)
                    reasons.append(f"deployed service {svc} telemetry healthy")
                else:
                    support += 1.6
                    for_ids.append(e.id)
        elif kind in _ASSERTS_UNHEALTHY:
            if svc == implicated:
                if healthy:
                    support -= 1.8
                    against.append(e.id)
                    reasons.append(f"implicated service {svc} telemetry healthy")
                else:
                    support += 1.8
                    for_ids.append(e.id)
            elif svc == affected and implicated != affected and not healthy and kind in _DEP_KINDS:
                support += 1.0
                for_ids.append(e.id)
                reasons.append(f"caller {affected} failing (consistent with {implicated} fault)")

    # -- deployment evidence ----------------------------------------------
    for e in signals.evidence:
        if e.source != EvidenceSource.DEPLOYMENT:
            continue
        dep_svc = e.service
        if kind == KIND_DEPLOYMENT and dep_svc == implicated:
            support += 2.2
            for_ids.append(e.id)
            reasons.append(f"deployment {e.refs.get('deployment_id')} on {dep_svc}")
            if _relevant_components(e.refs.get("changed_components")):
                support += 0.5
            mins = e.refs.get("minutes_before")
            if isinstance(mins, int) and mins <= 60:
                support += 0.5
            if str(e.refs.get("status")) in ("failed", "rolled_back"):
                support += 0.5
        elif kind in (KIND_DB, KIND_KAFKA, KIND_REDIS, KIND_PAYMENT, KIND_DEPENDENCY) and dep_svc == implicated:
            support += 0.4
            for_ids.append(e.id)

    # -- historical (inference) -------------------------------------------
    hist_matches = 0
    for e in signals.evidence:
        if e.source != EvidenceSource.HISTORICAL:
            continue
        if FINGERPRINT_KIND.get(str(e.refs.get("fingerprint"))) == kind:
            hist_matches += 1
            for_ids.append(e.id)
    if hist_matches:
        support += min(hist_matches * 0.5, 1.5)
        reasons.append(f"{hist_matches} historical incident(s) with matching pattern")

    # -- runbook (inference) ----------------------------------------------
    hints = _RUNBOOK_HINTS.get(kind, ())
    for e in signals.evidence:
        if e.source != EvidenceSource.RUNBOOK:
            continue
        rid = str(e.refs.get("runbook_id") or "").lower()
        if any(hint in rid for hint in hints):
            support += 0.4
            for_ids.append(e.id)
            break

    # -- human-supplied facts ---------------------------------------------
    for e in signals.evidence:
        if e.source != EvidenceSource.HUMAN:
            continue
        if any(kw in e.finding.lower() for kw in signals.keyword_hits(kind) or []):
            support += 0.8
            for_ids.append(e.id)
            reasons.append("operator-provided information aligns")

    h.support_score = round(support, 3)
    h.confidence_score = round(_confidence(support), 3)
    h.evidence_for = list(dict.fromkeys(for_ids))
    h.evidence_against = list(dict.fromkeys(against))
    h.rationale = "; ".join(reasons)


def _score_novel(h: Hypothesis, signals: Signals, best_specific: float) -> None:
    support = 1.3
    if best_specific < 2.5:
        support += 0.8
        h.rationale = "no strong specific signal; failure may be novel"
    if not signals.deployments:
        support += 0.3
    # weak historical overlap boosts novelty (few/low-relevance matches)
    h.support_score = round(support, 3)
    h.confidence_score = round(_confidence(support), 3)


def score_all(hypotheses: list[Hypothesis], signals: Signals) -> list[Hypothesis]:
    """Score every hypothesis in place (evidence validation + contradiction)."""
    specific = [h for h in hypotheses if h.kind != KIND_NOVEL]
    for h in specific:
        score_hypothesis(h, signals)
    best_specific = max((h.support_score for h in specific), default=0.0)
    for h in hypotheses:
        if h.kind == KIND_NOVEL:
            _score_novel(h, signals, best_specific)
    return hypotheses


def rank_hypotheses(hypotheses: list[Hypothesis]) -> list[Hypothesis]:
    return sorted(hypotheses, key=lambda h: (-h.support_score, h.id))


def score_and_rank(hypotheses: list[Hypothesis], signals: Signals) -> list[Hypothesis]:
    return rank_hypotheses(score_all(hypotheses, signals))


def assess_sufficiency(
    ranked: list[Hypothesis],
    signals: Signals,
    deps: InvestigationDeps,
) -> tuple[InvestigationStatus, list[MissingInformationItem], list[HumanQuestion]]:
    if not ranked:
        return InvestigationStatus.INSUFFICIENT_EVIDENCE, [], []

    primary = ranked[0]
    second = ranked[1] if len(ranked) > 1 else None
    margin = primary.support_score - (second.support_score if second else 0.0)
    fact_support = sum(
        1
        for eid in primary.evidence_for
        if (ev := signals.evidence_by_id(eid)) and ev.kind == EvidenceKind.FACT
    )

    sufficient = (
        primary.kind != KIND_NOVEL
        and primary.confidence_score >= deps.sufficiency_confidence
        and margin >= deps.sufficiency_margin
        and (fact_support >= 2 or primary.kind == KIND_DATA)
    )
    if sufficient:
        return InvestigationStatus.DIAGNOSIS_READY, [], []

    missing, questions = _build_missing(ranked, signals)
    return InvestigationStatus.INSUFFICIENT_EVIDENCE, missing, questions


_KIND_QUESTIONS = {
    KIND_DB: ("current DB connection-pool usage and any long-running/blocking queries"),
    KIND_KAFKA: ("current Kafka consumer state, partition lag, and whether OMS is publishing events"),
    KIND_REDIS: ("current Redis memory utilization and eviction/OOM events"),
    KIND_PAYMENT: ("upstream payment provider status and circuit-breaker state"),
    KIND_FRONTEND: ("browser console errors and which recent frontend change is live"),
    KIND_DEPLOYMENT: ("whether the suspected deployment's changes match the failing behavior"),
    KIND_DATA: ("scope of affected records and whether a recent logic/config change is implicated"),
    KIND_NETWORK: ("network path health (NAT/DNS/LB) between the affected services"),
    KIND_NOVEL: ("which downstream service first showed errors and the earliest error signature"),
}


def _build_missing(
    ranked: list[Hypothesis], signals: Signals
) -> tuple[list[MissingInformationItem], list[HumanQuestion]]:
    missing: list[MissingInformationItem] = []
    questions: list[HumanQuestion] = []
    top = ranked[:2]

    # ambiguity between the two leading hypotheses on different services
    if len(top) == 2 and top[0].implicated_service != top[1].implicated_service:
        detail = (
            f"Cannot reliably distinguish '{top[0].statement}' "
            f"({top[0].implicated_service}) from '{top[1].statement}' "
            f"({top[1].implicated_service})."
        )
        missing.append(
            MissingInformationItem(
                topic="root-cause service",
                detail=detail,
                why_needed="the two leading hypotheses implicate different services",
            )
        )
        questions.append(
            HumanQuestion(
                id="Q1",
                question=f"Which service first showed the primary symptom: "
                f"{top[0].implicated_service} or {top[1].implicated_service}?",
                why="disambiguates the two leading hypotheses",
            )
        )

    qid = len(questions) + 1
    for h in top:
        ask = _KIND_QUESTIONS.get(h.kind)
        if ask:
            missing.append(
                MissingInformationItem(
                    topic=h.kind,
                    detail=f"Need {ask}.",
                    why_needed=f"to confirm or rule out '{h.statement}'",
                )
            )
            questions.append(
                HumanQuestion(id=f"Q{qid}", question=f"Please provide {ask}.", why=h.statement)
            )
            qid += 1

    # stale/missing telemetry
    if signals.stale_services:
        missing.append(
            MissingInformationItem(
                topic="telemetry",
                detail=f"No current telemetry for: {', '.join(signals.stale_services)}.",
                why_needed="recent readings are needed to evaluate these services",
            )
        )
    return missing, questions
