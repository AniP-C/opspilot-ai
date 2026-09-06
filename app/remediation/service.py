"""RemediationService — orchestrates the whole Phase 3 lifecycle.

    InvestigationResult (Phase 2, reused)
        -> plan (ActionPlanner)
        -> evaluate (RiskPolicyEngine, deterministic)
        -> AUTO_EXECUTE | ASK_HUMAN | ESCALATE | REJECT
        -> [approval gate]
        -> execute (MockActionExecutor — never real infra by default)
        -> verify (VerificationEngine — telemetry says if it truly recovered)
        -> [rollback on failure, when supported]
        -> ServiceNow write-back + audit + feedback

The LLM/heuristic only reaches the planner (a *proposal*). Everything from the
policy engine onward is deterministic application code, and only the validated
executor can act.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.domain.models import TelemetrySnapshot
from app.investigation.models import InvestigationResult, InvestigationStatus
from app.logging_config import get_logger
from app.remediation.audit import AuditRecorder
from app.remediation.catalog import get_action, is_target_allowed
from app.remediation.executor import ActionExecutor, ExecutionRequest
from app.remediation.history import HistoricalStats
from app.remediation.mock_executor import MockActionExecutor
from app.remediation.models import (
    ActionProposal,
    ApprovalDecision,
    ApprovalRecord,
    AuditEventType,
    ExecutionStatus,
    PolicyDecision,
    PolicyResult,
    RemediationRecord,
    RemediationStatus,
    RemediationView,
    VerificationStatus,
)
from app.remediation.planner import ActionPlanner
from app.remediation.policy import RiskPolicyEngine
from app.remediation.store import RemediationStore
from app.remediation.verification import VerificationEngine

logger = get_logger(__name__)


class RemediationError(Exception):
    """A remediation lifecycle precondition was violated (maps to HTTP 409)."""


class RemediationService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        planner: ActionPlanner,
        policy: RiskPolicyEngine,
        executor: ActionExecutor,
        verifier: VerificationEngine,
        sn_writer,
        telemetry_source,
        service_repo,
        incident_repo,
    ) -> None:
        self.session = session
        self.settings = settings
        self.store = RemediationStore(session)
        self.planner = planner
        self.policy = policy
        self.executor = executor
        self.verifier = verifier
        self.sn_writer = sn_writer
        self.telemetry_source = telemetry_source
        self.service_repo = service_repo
        self.incident_repo = incident_repo

    # ==================================================================== #
    # PLAN + POLICY (+ auto-execute when eligible)
    # ==================================================================== #
    def plan(
        self, incident_id: str, *, investigation_id: str | None = None
    ) -> RemediationView:
        from app.investigation.engine import make_investigation_engine
        from app.investigation.store import InvestigationStore

        inv_store = InvestigationStore(self.session)
        result: InvestigationResult
        if investigation_id and (existing := inv_store.get_result(investigation_id)):
            result = existing
        else:
            engine = make_investigation_engine(self.session, self.settings)
            result = engine.investigate(incident_id)  # raises IncidentNotFound -> 404
            inv_store.save(result, human_facts=[])

        incident = self.incident_repo.get_incident(incident_id)
        if incident is None:  # pragma: no cover - investigation would already 404
            from app.domain.errors import IncidentNotFound

            raise IncidentNotFound(incident_id)

        audit = AuditRecorder(self.store, incident_id)
        audit.emit(
            AuditEventType.INVESTIGATION_COMPLETED,
            f"Investigation {result.investigation_id} completed: "
            f"{result.status.value} (confidence {result.confidence_score:.2f})",
            detail={
                "investigation_id": result.investigation_id,
                "diagnosis": result.primary_hypothesis,
                "status": result.status.value,
            },
        )

        proposal = self.planner.plan(
            result,
            affected_service=incident.service,
            verification_window_seconds=self.settings.verification_window_seconds,
        )

        target_criticality: str | None = None
        allow_listed = False
        if proposal is not None:
            svc = self.service_repo.get_service(proposal.target_service)
            target_criticality = svc.criticality.value if svc else "unknown"
            allow_listed = is_target_allowed(proposal.action_id, proposal.target_service)
            audit.emit(
                AuditEventType.ACTION_PROPOSED,
                f"Proposed {proposal.action_id} on {proposal.target_service} "
                f"(risk {proposal.risk_level.value}, blast {proposal.blast_radius.value})",
                detail={
                    "action_id": proposal.action_id,
                    "target": proposal.target_service,
                    "evidence_ids": proposal.evidence_ids,
                    "recurrence": proposal.recurrence_count,
                    "historical_success": f"{proposal.historical_success}/{proposal.historical_total}",
                },
            )

        policy_result = self.policy.evaluate(
            proposal,
            diagnosis_ready=result.status == InvestigationStatus.DIAGNOSIS_READY,
            confidence=result.confidence_score,
            contradicting_evidence=bool(result.contradicting_evidence),
            target_criticality=target_criticality,
            target_allow_listed=allow_listed,
            environment=incident.environment.value,
        )

        action_ref = self._action_ref(incident_id, proposal)
        existing = self.store.get_record(action_ref)
        if existing and existing.execution is not None:
            # Already executed — do not clobber a completed remediation on re-plan.
            return self.get_record_view(action_ref)

        status = self._status_for_decision(policy_result.decision)
        record = RemediationRecord(
            action_ref=action_ref,
            incident_id=incident_id,
            investigation_id=result.investigation_id,
            status=status,
            proposal=proposal,
            policy=policy_result,
            created_at=datetime.utcnow(),
        )
        self.store.save_record(record)

        audit.emit(
            AuditEventType.POLICY_EVALUATED,
            f"Policy decision: {policy_result.decision.value.upper()}",
            action_ref=action_ref,
            detail={"decision": policy_result.decision.value, "reasons": policy_result.reasons},
        )

        if policy_result.decision == PolicyDecision.ASK_HUMAN:
            audit.emit(
                AuditEventType.APPROVAL_REQUESTED,
                f"Human approval requested for {proposal.action_id if proposal else 'action'}",
                action_ref=action_ref,
            )
        elif policy_result.decision == PolicyDecision.ESCALATE:
            audit.emit(
                AuditEventType.ESCALATED,
                "Escalated to a human incident commander.",
                action_ref=action_ref,
            )
        elif policy_result.decision == PolicyDecision.AUTO_EXECUTE:
            audit.emit(
                AuditEventType.ACTION_AUTO_APPROVED,
                f"Auto-execution authorised by policy for {proposal.action_id}",
                action_ref=action_ref,
            )
            # Safe auto-action: execute immediately, then verify.
            self._execute_and_verify(record, approval_verified=True, actor="policy:auto")

        return self.get_record_view(action_ref)

    # ==================================================================== #
    # APPROVAL GATE
    # ==================================================================== #
    def approve(
        self, action_ref: str, *, approver: str = "operator", note: str = ""
    ) -> RemediationView:
        record = self._require(action_ref)
        if record.status != RemediationStatus.AWAITING_APPROVAL:
            raise RemediationError(
                f"Action {action_ref} is not awaiting approval (status={record.status.value})."
            )
        approval = ApprovalRecord(
            action_ref=action_ref,
            decision=ApprovalDecision.APPROVE,
            approver=approver,
            note=note,
            decided_at=datetime.utcnow(),
        )
        self.store.record_approval(approval)
        record.approval = approval
        record.status = RemediationStatus.APPROVED
        self.store.save_record(record)
        AuditRecorder(self.store, record.incident_id).emit(
            AuditEventType.ACTION_APPROVED,
            f"Action approved by {approver}",
            action_ref=action_ref,
            actor=approver,
            detail={"note": note},
        )
        self._execute_and_verify(record, approval_verified=True, actor=approver)
        return self.get_record_view(action_ref)

    def reject(
        self, action_ref: str, *, approver: str = "operator", note: str = ""
    ) -> RemediationView:
        record = self._require(action_ref)
        if record.status != RemediationStatus.AWAITING_APPROVAL:
            raise RemediationError(
                f"Action {action_ref} is not awaiting approval (status={record.status.value})."
            )
        approval = ApprovalRecord(
            action_ref=action_ref,
            decision=ApprovalDecision.REJECT,
            approver=approver,
            note=note,
            decided_at=datetime.utcnow(),
        )
        self.store.record_approval(approval)
        record.approval = approval
        record.status = RemediationStatus.REJECTED
        self.store.save_record(record)
        AuditRecorder(self.store, record.incident_id).emit(
            AuditEventType.ACTION_REJECTED,
            f"Action rejected by {approver}",
            action_ref=action_ref,
            actor=approver,
            detail={"note": note},
        )
        self.store.record_feedback(action_ref, record.incident_id, "rejected", note)
        return self.get_record_view(action_ref)

    def investigate_further(self, action_ref: str, information: str) -> RemediationView:
        """The 'INVESTIGATE FURTHER' path: feed operator info back into Phase 2 and
        re-plan with the merged facts."""
        from app.investigation.engine import make_investigation_engine
        from app.investigation.store import InvestigationStore

        record = self._require(action_ref)
        inv_store = InvestigationStore(self.session)
        prior = inv_store.get(record.investigation_id) if record.investigation_id else None
        human_facts = list(prior.human_facts or []) if prior else []
        human_facts.append(information)
        engine = make_investigation_engine(self.session, self.settings)
        new_result = engine.investigate(
            record.incident_id,
            investigation_id=record.investigation_id,
            human_facts=human_facts,
        )
        inv_store.save(new_result, human_facts=human_facts)
        AuditRecorder(self.store, record.incident_id).emit(
            AuditEventType.INVESTIGATION_COMPLETED,
            "Re-investigated with additional operator information.",
            action_ref=action_ref,
            detail={"information": information},
        )
        return self.plan(record.incident_id, investigation_id=record.investigation_id)

    # ==================================================================== #
    # EXPLICIT EXECUTE / ROLLBACK
    # ==================================================================== #
    def execute(self, action_ref: str, *, override: str | None = None) -> RemediationView:
        record = self._require(action_ref)
        if record.status not in (RemediationStatus.APPROVED, RemediationStatus.AUTO_APPROVED):
            raise RemediationError(
                f"Action {action_ref} is not authorised for execution (status={record.status.value})."
            )
        self._execute_and_verify(record, approval_verified=True, actor="operator", override=override)
        return self.get_record_view(action_ref)

    def rollback(self, action_ref: str) -> RemediationView:
        record = self._require(action_ref)
        if record.execution is None:
            raise RemediationError(f"Action {action_ref} has no execution to roll back.")
        self._do_rollback(record, actor="operator")
        return self.get_record_view(action_ref)

    # ==================================================================== #
    # INTERNAL: execute -> verify -> servicenow -> feedback
    # ==================================================================== #
    def _execute_and_verify(
        self,
        record: RemediationRecord,
        *,
        approval_verified: bool,
        actor: str,
        override: str | None = None,
    ) -> None:
        proposal = record.proposal
        action = get_action(proposal.action_id) if proposal else None
        if proposal is None or action is None:
            raise RemediationError("No executable action on this record.")

        audit = AuditRecorder(self.store, record.incident_id)
        idem_key = f"{record.action_ref}:{proposal.target_service}:attempt1"

        # Idempotency: never execute the same action twice.
        prior = self.store.find_execution_by_key(idem_key)
        if prior is not None and prior.status != ExecutionStatus.SKIPPED:
            record.execution = prior
            self.store.save_record(record)
            audit.emit(
                AuditEventType.ACTION_STARTED,
                "Duplicate execution suppressed by idempotency key (already executed).",
                action_ref=record.action_ref,
                detail={"idempotency_key": idem_key},
            )
            return

        record.status = RemediationStatus.EXECUTING
        self.store.save_record(record)
        audit.emit(
            AuditEventType.ACTION_STARTED,
            f"Executing {proposal.action_id} on {proposal.target_service} "
            f"(mode={self.executor.mode})",
            action_ref=record.action_ref,
            actor=actor,
        )

        before = self._telemetry(proposal.target_service, record.incident_id)
        request = ExecutionRequest(
            incident_id=record.incident_id,
            proposal=proposal,
            action=action,
            idempotency_key=idem_key,
            action_ref=record.action_ref,
            before=before,
            approval_verified=approval_verified,
            mode=self.executor.mode,
            outcome_override=override,
        )
        result = self.executor.execute(request)
        self.store.save_execution(result)
        record.execution = result

        if result.status != ExecutionStatus.SUCCEEDED:
            record.status = RemediationStatus.EXECUTION_FAILED
            self.store.save_record(record)
            audit.emit(
                AuditEventType.ACTION_FAILED,
                f"Execution {result.status.value}: {result.message}",
                action_ref=record.action_ref,
                detail={"lifecycle": result.lifecycle},
            )
            self._servicenow_note(record, resolved=False)
            self.store.record_feedback(
                record.action_ref, record.incident_id, "execution_failed", result.message
            )
            if action.rollback_supported:
                self._do_rollback(record, actor="system")
            return

        record.status = RemediationStatus.EXECUTED
        self.store.save_record(record)
        audit.emit(
            AuditEventType.ACTION_COMPLETED,
            f"Executor reported success for {proposal.action_id} (NOT yet verified).",
            action_ref=record.action_ref,
            detail={"lifecycle": result.lifecycle},
        )

        # ---- Verification (execution success != recovery) ------------------
        record.status = RemediationStatus.VERIFYING
        self.store.save_record(record)
        audit.emit(
            AuditEventType.VERIFICATION_STARTED,
            f"Verifying recovery for {proposal.target_service}.",
            action_ref=record.action_ref,
        )
        before_snap = TelemetrySnapshot.model_validate(result.before) if result.before else None
        after_snap = TelemetrySnapshot.model_validate(result.after) if result.after else None
        verification = self.verifier.verify(
            proposal.verification_plan,
            before_snap,
            after_snap,
            duration_seconds=(
                proposal.verification_plan.observation_window_seconds
                if proposal.verification_plan
                else 0
            ),
        )
        verification.action_ref = record.action_ref
        self.store.save_verification(verification, result.execution_id)
        record.verification = verification

        if verification.status == VerificationStatus.PASSED:
            record.status = RemediationStatus.VERIFIED
            self.store.save_record(record)
            audit.emit(
                AuditEventType.VERIFICATION_PASSED,
                f"Verified recovered: {verification.reason}",
                action_ref=record.action_ref,
                detail={"observations": [o.model_dump() for o in verification.observations]},
            )
            self._servicenow_note(record, resolved=True)
            self.store.record_feedback(
                record.action_ref, record.incident_id, "recovered", verification.reason
            )
        else:
            self.store.save_record(record)
            audit.emit(
                AuditEventType.VERIFICATION_FAILED,
                f"Verification {verification.status.value}: {verification.reason}",
                action_ref=record.action_ref,
                detail={"observations": [o.model_dump() for o in verification.observations]},
            )
            self._servicenow_note(record, resolved=False)
            if action.rollback_supported:
                self._do_rollback(record, actor="system")
            else:
                record.status = RemediationStatus.VERIFICATION_FAILED
                self.store.save_record(record)
                self.store.record_feedback(
                    record.action_ref, record.incident_id, "verification_failed", verification.reason
                )

    def _do_rollback(self, record: RemediationRecord, *, actor: str) -> None:
        proposal = record.proposal
        action = get_action(proposal.action_id) if proposal else None
        audit = AuditRecorder(self.store, record.incident_id)
        if proposal is None or action is None or record.execution is None:
            return
        audit.emit(
            AuditEventType.ROLLBACK_STARTED,
            f"Rolling back {proposal.action_id} on {proposal.target_service}.",
            action_ref=record.action_ref,
            actor=actor,
        )
        request = ExecutionRequest(
            incident_id=record.incident_id,
            proposal=proposal,
            action=action,
            idempotency_key=f"{record.action_ref}:rollback",
            action_ref=record.action_ref,
            approval_verified=True,
            mode=self.executor.mode,
        )
        rolled = self.executor.rollback(request, record.execution)
        self.store.save_execution(rolled)
        record.execution = rolled
        if rolled.rolled_back:
            record.status = RemediationStatus.ROLLED_BACK
            self.store.save_record(record)
            audit.emit(
                AuditEventType.ROLLBACK_COMPLETED,
                "Rollback completed; system restored to the pre-action state.",
                action_ref=record.action_ref,
                detail={"lifecycle": rolled.rollback_lifecycle},
            )
            self.store.record_feedback(
                record.action_ref, record.incident_id, "rolled_back", "verification/execution failed"
            )
        else:
            record.status = RemediationStatus.ROLLBACK_FAILED
            self.store.save_record(record)
            audit.emit(
                AuditEventType.ROLLBACK_FAILED,
                "Rollback not supported / failed — manual remediation required.",
                action_ref=record.action_ref,
            )

    def _servicenow_note(self, record: RemediationRecord, *, resolved: bool) -> None:
        proposal = record.proposal
        if proposal is None:
            return
        note = self._compose_rca_note(record, resolved=resolved)
        try:
            self.sn_writer.add_work_note(record.incident_id, note)
            if resolved:
                self.sn_writer.update_state(record.incident_id, "6")  # 6 == Resolved
            record.servicenow_updated = True
            self.store.save_record(record)
            AuditRecorder(self.store, record.incident_id).emit(
                AuditEventType.SERVICENOW_UPDATED,
                "ServiceNow updated with remediation work note"
                + (" and status=Resolved." if resolved else "."),
                action_ref=record.action_ref,
                detail={"work_note": note, "resolved": resolved, "mode": getattr(self.sn_writer, "mode", "mock")},
            )
        except Exception as exc:  # noqa: BLE001 - write-back is best-effort
            logger.warning("servicenow_write: failed (non-fatal): %s", exc)

    @staticmethod
    def _compose_rca_note(record: RemediationRecord, *, resolved: bool) -> str:
        p = record.proposal
        pol = record.policy
        ver = record.verification
        lines = [
            "[OpsPilot AI — automated remediation]",
            f"Diagnosis: {p.rationale if p else 'n/a'}",
            f"Action: {p.action_id} on {p.target_service}" if p else "Action: n/a",
            f"Policy decision: {pol.decision.value.upper()}" if pol else "",
            f"Recurrence: {p.recurrence_count}; historical success: {p.historical_success}/{p.historical_total}"
            if p
            else "",
            f"Verification: {ver.status.value} — {ver.reason}" if ver else "Verification: pending",
            f"Outcome: {'INCIDENT RECOVERED' if resolved else 'NOT recovered — see rollback/escalation'}",
        ]
        return "\n".join(line for line in lines if line)

    # ==================================================================== #
    # READ
    # ==================================================================== #
    def get_view(self, incident_id: str) -> RemediationView:
        record = self.store.latest_for_incident(incident_id)
        return self._build_view(incident_id, record)

    def get_record_view(self, action_ref: str) -> RemediationView:
        record = self.store.get_record(action_ref)
        incident_id = record.incident_id if record else action_ref.split("::")[0]
        return self._build_view(incident_id, record)

    def _build_view(self, incident_id: str, record: RemediationRecord | None) -> RemediationView:
        diagnosis = confidence = inv_status = inv_id = None
        if record and record.investigation_id:
            from app.investigation.store import InvestigationStore

            res = InvestigationStore(self.session).get_result(record.investigation_id)
            if res:
                diagnosis = res.primary_hypothesis
                confidence = res.confidence_score
                inv_status = res.status.value
                inv_id = res.investigation_id
        return RemediationView(
            incident_id=incident_id,
            investigation_id=inv_id,
            diagnosis=diagnosis,
            confidence=confidence or 0.0,
            investigation_status=inv_status,
            record=record,
            audit=self.store.list_audit(incident_id),
        )

    # -- helpers -----------------------------------------------------------
    def _telemetry(self, service: str, incident_id: str) -> TelemetrySnapshot | None:
        incident = self.incident_repo.get_incident(incident_id)
        at = incident.created_at if incident else None
        try:
            return self.telemetry_source.get_service_health(service, at)
        except Exception:  # noqa: BLE001 - telemetry is best-effort context
            return None

    @staticmethod
    def _action_ref(incident_id: str, proposal: ActionProposal | None) -> str:
        return f"{incident_id}::{proposal.action_id if proposal else 'no-action'}"

    @staticmethod
    def _status_for_decision(decision: PolicyDecision) -> RemediationStatus:
        return {
            PolicyDecision.AUTO_EXECUTE: RemediationStatus.AUTO_APPROVED,
            PolicyDecision.ASK_HUMAN: RemediationStatus.AWAITING_APPROVAL,
            PolicyDecision.ESCALATE: RemediationStatus.ESCALATED,
            PolicyDecision.REJECT: RemediationStatus.REJECTED_BY_POLICY,
        }[decision]

    def _require(self, action_ref: str) -> RemediationRecord:
        record = self.store.get_record(action_ref)
        if record is None:
            raise RemediationError(f"Remediation action '{action_ref}' not found.")
        return record


# --------------------------------------------------------------------------- #
# Factory — wires the service, honouring the mock/real safety flags.
# --------------------------------------------------------------------------- #
def make_remediation_service(
    session: Session, settings: Settings | None = None
) -> RemediationService:
    from app.api.dependencies import make_context_builder
    from app.db.repositories import IncidentRepository, ServiceRepository
    from app.integrations.servicenow.mock_client import MockServiceNowWriter

    settings = settings or get_settings()
    builder = make_context_builder(session, settings)

    historical = HistoricalStats.from_data_dir(settings.data_dir)
    planner = ActionPlanner(historical)
    policy = RiskPolicyEngine(
        auto_execute_min_confidence=settings.auto_execute_min_confidence,
        auto_execute_min_recurrence=settings.auto_execute_min_recurrence,
        auto_execute_min_success_rate=settings.auto_execute_min_success_rate,
    )

    # Executor: mock is the hard default. Real infrastructure execution is not
    # implemented in this prototype (there is no real platform to mutate), so even
    # if the real flags were set we never mutate — we use the safe mock executor.
    executor: ActionExecutor = MockActionExecutor()
    if not settings.execution_is_mock:
        logger.warning(
            "execution: real mode requested but real execution is not implemented; "
            "using the safe mock executor (no infrastructure is mutated)."
        )

    # ServiceNow writer: real write-back only when explicitly enabled; else mock.
    if settings.servicenow_write_is_mock:
        sn_writer = MockServiceNowWriter()
    else:  # pragma: no cover - requires real credentials
        from app.integrations.servicenow.client import ServiceNowWriter

        sn_writer = ServiceNowWriter(
            settings.servicenow_url, settings.servicenow_user, settings.servicenow_token
        )

    return RemediationService(
        session,
        settings,
        planner=planner,
        policy=policy,
        executor=executor,
        verifier=VerificationEngine(),
        sn_writer=sn_writer,
        telemetry_source=builder.telemetry_source,
        service_repo=ServiceRepository(session),
        incident_repo=IncidentRepository(session),
    )
