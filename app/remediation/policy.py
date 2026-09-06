"""The deterministic risk / policy engine.

This is ordinary, fixed application logic — NOT an LLM. The model (or heuristic)
proposes an action; this engine decides whether it is allowed, and produces one
of four explicit, explainable outcomes:

    AUTO_EXECUTE   — safe to run without a human (only LOW-risk, high-confidence,
                     well-precedented, reversible/verifiable, contained-blast actions)
    ASK_HUMAN      — a human must approve before execution
    ESCALATE       — hand to a human incident commander (no safe automated path,
                     or critical/high-risk on a critical service, or no diagnosis)
    REJECT         — not permitted at all (e.g. target not allow-listed)

The rules are intentionally conservative: anything that does not clearly qualify
for AUTO_EXECUTE requires a human. The most important guarantee is that the
unsafe-auto-execution rate is zero — HIGH/CRITICAL actions can never be
auto-executed, and neither can anything with contradicting evidence, weak
precedent, or an unverifiable/irreversible effect.
"""

from __future__ import annotations

from app.remediation.models import (
    ActionProposal,
    ActionRisk,
    BlastRadius,
    PolicyDecision,
    PolicyResult,
    PolicySignals,
)

# Blast radii that are "contained" enough to be eligible for auto-execution.
_CONTAINED_BLAST = {BlastRadius.SINGLE_SERVICE, BlastRadius.DEPENDENTS}


class RiskPolicyEngine:
    def __init__(
        self,
        *,
        auto_execute_min_confidence: float = 0.90,
        auto_execute_min_recurrence: int = 3,
        auto_execute_min_success_rate: float = 0.80,
    ) -> None:
        self.min_conf = auto_execute_min_confidence
        self.min_recurrence = auto_execute_min_recurrence
        self.min_success = auto_execute_min_success_rate

    def evaluate(
        self,
        proposal: ActionProposal | None,
        *,
        diagnosis_ready: bool,
        confidence: float,
        contradicting_evidence: bool,
        target_criticality: str | None,
        target_allow_listed: bool,
        environment: str = "production",
    ) -> PolicyResult:
        # ---- No concrete, allow-listed action to consider -> ESCALATE -------
        if proposal is None:
            reasons = (
                ["Investigation did not reach a confident diagnosis — no automated action is proposed."]
                if not diagnosis_ready
                else ["No safe, allow-listed remediation exists for this diagnosis (novel/uncatalogued pattern)."]
            )
            reasons.append("Escalating to a human incident commander.")
            signals = PolicySignals(
                diagnosis_ready=diagnosis_ready,
                confidence=confidence,
                contradicting_evidence=contradicting_evidence,
                environment=environment,
            )
            return PolicyResult(
                decision=PolicyDecision.ESCALATE,
                reasons=reasons,
                signals=signals,
                requires_human_approval=True,
                auto_execute_eligible=False,
            )

        reversible = proposal.rollback_supported
        verification_possible = bool(
            proposal.verification_plan and proposal.verification_plan.metrics
        )
        success_rate = proposal.historical_success_rate
        signals = PolicySignals(
            diagnosis_ready=diagnosis_ready,
            confidence=confidence,
            action_id=proposal.action_id,
            action_risk=proposal.risk_level,
            blast_radius=proposal.blast_radius,
            target_service=proposal.target_service,
            target_criticality=target_criticality,
            target_allow_listed=target_allow_listed,
            recurrence_count=proposal.recurrence_count,
            historical_success_rate=success_rate,
            contradicting_evidence=contradicting_evidence,
            reversible=reversible,
            verification_possible=verification_possible,
            environment=environment,
        )

        reasons: list[str] = [
            f"diagnosis {'ready' if diagnosis_ready else 'insufficient'} (confidence {confidence:.2f})",
            f"action risk = {proposal.risk_level.value.upper()}",
            f"blast radius = {proposal.blast_radius.value}",
            f"target {proposal.target_service} criticality = {target_criticality or 'unknown'}",
            f"recurrence = {proposal.recurrence_count}"
            + (
                f", historical success = {proposal.historical_success}/{proposal.historical_total}"
                if proposal.historical_total
                else ", no recorded precedent"
            ),
            f"rollback available = {'yes' if reversible else 'no'}",
            f"verification possible = {'yes' if verification_possible else 'no'}",
            f"environment = {environment}",
        ]
        if contradicting_evidence:
            reasons.append("contradicting evidence present against the primary hypothesis")

        # ---- Gate: target must be allow-listed (defence in depth) -----------
        if not target_allow_listed:
            reasons.append(f"REJECT: {proposal.target_service} is not allow-listed for {proposal.action_id}")
            return self._result(PolicyDecision.REJECT, reasons, signals)

        risk = proposal.risk_level

        # ---- CRITICAL: never automated -------------------------------------
        if risk == ActionRisk.CRITICAL:
            reasons.append("CRITICAL-risk action — escalation to a human incident commander is mandatory.")
            return self._result(PolicyDecision.ESCALATE, reasons, signals)

        # ---- HIGH: escalate on a critical service, else human approval ------
        if risk == ActionRisk.HIGH:
            if (target_criticality or "").lower() == "critical":
                reasons.append(
                    "HIGH-risk action on a business-critical service — escalate; "
                    "no safe automated precedent for this remediation."
                )
                return self._result(PolicyDecision.ESCALATE, reasons, signals)
            reasons.append("HIGH-risk action — explicit human approval required.")
            return self._result(PolicyDecision.ASK_HUMAN, reasons, signals)

        # ---- MEDIUM: always human approval ---------------------------------
        if risk == ActionRisk.MEDIUM:
            reasons.append("MEDIUM-risk action — human approval required before execution.")
            return self._result(PolicyDecision.ASK_HUMAN, reasons, signals)

        # ---- LOW: the only auto-execute candidate --------------------------
        blockers: list[str] = []
        if not diagnosis_ready:
            blockers.append("diagnosis not ready")
        if confidence < self.min_conf:
            blockers.append(f"confidence {confidence:.2f} < {self.min_conf:.2f}")
        if proposal.recurrence_count < self.min_recurrence:
            blockers.append(
                f"insufficient precedent (recurrence {proposal.recurrence_count} < {self.min_recurrence})"
            )
        if success_rate is None or success_rate < self.min_success:
            blockers.append(
                f"historical success rate {success_rate if success_rate is not None else 'n/a'} "
                f"< {self.min_success:.2f}"
            )
        if contradicting_evidence:
            blockers.append("contradicting evidence present")
        if not (reversible or verification_possible):
            blockers.append("action is neither reversible nor verifiable")
        if proposal.blast_radius not in _CONTAINED_BLAST:
            blockers.append(f"blast radius {proposal.blast_radius.value} is not contained")

        if not blockers:
            reasons.append(
                "LOW-risk, high-confidence, well-precedented, verifiable, contained action — "
                "eligible for safe auto-execution."
            )
            return self._result(PolicyDecision.AUTO_EXECUTE, reasons, signals)

        reasons.append("LOW-risk but not auto-eligible: " + "; ".join(blockers) + ".")
        reasons.append("Routing to human approval.")
        return self._result(PolicyDecision.ASK_HUMAN, reasons, signals)

    @staticmethod
    def _result(
        decision: PolicyDecision, reasons: list[str], signals: PolicySignals
    ) -> PolicyResult:
        return PolicyResult(
            decision=decision,
            reasons=reasons,
            signals=signals,
            requires_human_approval=decision == PolicyDecision.ASK_HUMAN,
            auto_execute_eligible=decision == PolicyDecision.AUTO_EXECUTE,
        )
