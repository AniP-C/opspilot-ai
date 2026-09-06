"""Evaluation metrics for investigations (TEST/EVAL ONLY).

Provides the *framework* to measure quality — we do not claim final accuracy
here. Metrics:
  * top-1 diagnosis-service correctness
  * deployment-correlation correctness
  * evidence grounding (are conclusions traceable to evidence?)
  * hallucination guard (did any ground-truth string leak into reasoning?)
  * missing-information detection (did it ask when uncertain?)
  * human-intervention requirement
  * latency
"""

from __future__ import annotations

from app.evaluation.ground_truth import expected_correlation
from app.investigation.models import CorrelationLevel, InvestigationResult, InvestigationStatus


def evaluate_result(result: InvestigationResult, gt: dict) -> dict:
    primary = result.hypotheses[0] if result.hypotheses else None
    implicated = primary.implicated_service if primary else None
    expected_service = gt.get("expected_service")

    # top-1 service correctness
    top1_correct = implicated == expected_service if expected_service else None

    # deployment-correlation correctness
    corr_expected = expected_correlation(gt)
    corr_level = result.deployment_correlation.level if result.deployment_correlation else CorrelationLevel.NONE
    corr_correct: bool | None
    if corr_expected == "true":
        corr_correct = corr_level in (CorrelationLevel.HIGH, CorrelationLevel.MEDIUM)
    elif corr_expected == "false":
        corr_correct = corr_level in (CorrelationLevel.LOW, CorrelationLevel.NONE)
    else:
        corr_correct = None

    # evidence grounding: primary hypothesis has traceable supporting evidence
    evidence_ids = {e.id for e in result.evidence}
    primary_grounded = bool(primary and primary.evidence_for) and all(
        eid in evidence_ids for eid in (primary.evidence_for if primary else [])
    )
    grounded_fraction = (
        sum(1 for h in result.hypotheses if h.evidence_for) / len(result.hypotheses)
        if result.hypotheses
        else 0.0
    )

    # hallucination guard: no ground-truth string should appear in the output
    leaked = _ground_truth_leaked(result, gt)

    return {
        "incident_id": result.incident_id,
        "status": result.status.value,
        "primary_service": implicated,
        "expected_service": expected_service,
        "top1_service_correct": top1_correct,
        "confidence": result.confidence_score,
        "deployment_correlation": corr_level.value,
        "deployment_correlation_expected": corr_expected,
        "deployment_correlation_correct": corr_correct,
        "primary_grounded": primary_grounded,
        "grounded_fraction": round(grounded_fraction, 3),
        "asked_for_more_info": result.status == InvestigationStatus.INSUFFICIENT_EVIDENCE,
        "human_approval_required": result.human_approval_required,
        "ground_truth_leaked": leaked,
        "latency_ms": result.duration_ms,
        "num_hypotheses": len(result.hypotheses),
    }


def _ground_truth_leaked(result: InvestigationResult, gt: dict) -> bool:
    """True if the (hidden) actual root cause text appears in the diagnosis."""
    actual = str(gt.get("actual_root_cause", "")).strip().lower()
    if not actual:
        return False
    blob = " ".join(
        filter(None, [result.primary_hypothesis or "", result.recommended_next_action or ""])
    ).lower()
    return actual in blob


def aggregate(reports: list[dict]) -> dict:
    def _rate(key: str) -> float | None:
        vals = [r[key] for r in reports if r.get(key) is not None]
        return round(sum(1 for v in vals if v) / len(vals), 3) if vals else None

    latencies = [r["latency_ms"] for r in reports if r.get("latency_ms") is not None]
    return {
        "n": len(reports),
        "top1_service_accuracy": _rate("top1_service_correct"),
        "deployment_correlation_accuracy": _rate("deployment_correlation_correct"),
        "primary_grounded_rate": _rate("primary_grounded"),
        "hallucination_rate": (
            round(sum(1 for r in reports if r.get("ground_truth_leaked")) / len(reports), 3)
            if reports
            else None
        ),
        "asked_for_more_info_count": sum(1 for r in reports if r.get("asked_for_more_info")),
        "mean_latency_ms": int(sum(latencies) / len(latencies)) if latencies else None,
    }
