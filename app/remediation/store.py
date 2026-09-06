"""Persistence for the remediation lifecycle (reuses the Phase 1 DB layer).

Maps the aggregate ``RemediationRecord`` <-> ``RemediationActionORM`` and records
the individual transitions (approvals, executions, verifications, audit, feedback)
in their own tables. Execution idempotency is enforced here: an execution is keyed
by a unique ``idempotency_key`` so a retry / duplicate request never runs twice.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    ActionApprovalORM,
    ActionExecutionORM,
    ActionFeedbackORM,
    AuditEventORM,
    RemediationActionORM,
    VerificationResultORM,
)
from app.remediation.models import (
    ActionProposal,
    ApprovalRecord,
    AuditEvent,
    AuditEventType,
    ExecutionResult,
    PolicyResult,
    RemediationRecord,
    RemediationStatus,
    VerificationResult,
)


def _dump(model) -> dict | None:
    return model.model_dump(mode="json") if model is not None else None


class RemediationStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    # -- aggregate record --------------------------------------------------
    def save_record(self, record: RemediationRecord) -> RemediationRecord:
        now = datetime.utcnow()
        row = self.session.get(RemediationActionORM, record.action_ref)
        if row is None:
            row = RemediationActionORM(
                action_ref=record.action_ref,
                incident_id=record.incident_id,
                investigation_id=record.investigation_id,
                created_at=record.created_at or now,
            )
            self.session.add(row)
        row.incident_id = record.incident_id
        row.investigation_id = record.investigation_id
        row.status = record.status.value
        row.proposal = _dump(record.proposal)
        row.policy = _dump(record.policy)
        row.approval = _dump(record.approval)
        row.execution = _dump(record.execution)
        row.verification = _dump(record.verification)
        row.servicenow_updated = record.servicenow_updated
        row.updated_at = now
        record.updated_at = now
        record.created_at = row.created_at
        self.session.commit()
        return record

    def get_record(self, action_ref: str) -> RemediationRecord | None:
        row = self.session.get(RemediationActionORM, action_ref)
        return self._to_record(row) if row else None

    def latest_for_incident(self, incident_id: str) -> RemediationRecord | None:
        rows = self.session.scalars(
            select(RemediationActionORM)
            .where(RemediationActionORM.incident_id == incident_id)
            .order_by(RemediationActionORM.updated_at.desc())
        ).all()
        return self._to_record(rows[0]) if rows else None

    @staticmethod
    def _to_record(row: RemediationActionORM) -> RemediationRecord:
        return RemediationRecord(
            action_ref=row.action_ref,
            incident_id=row.incident_id,
            investigation_id=row.investigation_id,
            status=RemediationStatus(row.status) if row.status else RemediationStatus.PROPOSED,
            proposal=ActionProposal.model_validate(row.proposal) if row.proposal else None,
            policy=PolicyResult.model_validate(row.policy) if row.policy else None,
            approval=ApprovalRecord.model_validate(row.approval) if row.approval else None,
            execution=ExecutionResult.model_validate(row.execution) if row.execution else None,
            verification=VerificationResult.model_validate(row.verification) if row.verification else None,
            servicenow_updated=bool(row.servicenow_updated),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    # -- approvals ---------------------------------------------------------
    def record_approval(self, approval: ApprovalRecord) -> None:
        self.session.add(
            ActionApprovalORM(
                action_ref=approval.action_ref,
                decision=approval.decision.value,
                approver=approval.approver,
                note=approval.note,
                decided_at=approval.decided_at or datetime.utcnow(),
            )
        )
        self.session.commit()

    # -- executions (idempotency) -----------------------------------------
    def find_execution_by_key(self, idempotency_key: str) -> ExecutionResult | None:
        row = self.session.scalar(
            select(ActionExecutionORM).where(
                ActionExecutionORM.idempotency_key == idempotency_key
            )
        )
        return ExecutionResult.model_validate(row.result) if row and row.result else None

    def save_execution(self, execution: ExecutionResult) -> None:
        row = self.session.get(ActionExecutionORM, execution.execution_id)
        if row is None:
            row = ActionExecutionORM(
                execution_id=execution.execution_id,
                idempotency_key=execution.idempotency_key,
                created_at=datetime.utcnow(),
            )
            self.session.add(row)
        row.action_ref = execution.action_ref
        row.mode = execution.mode
        row.status = execution.status.value
        row.attempt = execution.attempt
        row.result = execution.model_dump(mode="json")
        self.session.commit()

    # -- verifications -----------------------------------------------------
    def save_verification(
        self, verification: VerificationResult, execution_id: str | None
    ) -> None:
        self.session.add(
            VerificationResultORM(
                action_ref=verification.action_ref,
                execution_id=execution_id,
                status=verification.status.value,
                result=verification.model_dump(mode="json"),
                created_at=datetime.utcnow(),
            )
        )
        self.session.commit()

    # -- audit -------------------------------------------------------------
    def append_audit(self, event: AuditEvent) -> AuditEvent:
        at = event.at or datetime.utcnow()
        self.session.add(
            AuditEventORM(
                incident_id=event.incident_id,
                action_ref=event.action_ref,
                event_type=event.event_type.value,
                summary=event.summary,
                detail=event.detail,
                actor=event.actor,
                at=at,
            )
        )
        self.session.commit()
        event.at = at
        return event

    def list_audit(self, incident_id: str) -> list[AuditEvent]:
        rows = self.session.scalars(
            select(AuditEventORM)
            .where(AuditEventORM.incident_id == incident_id)
            .order_by(AuditEventORM.id)
        ).all()
        return [
            AuditEvent(
                incident_id=r.incident_id,
                action_ref=r.action_ref,
                event_type=AuditEventType(r.event_type),
                summary=r.summary,
                detail=dict(r.detail or {}),
                actor=r.actor,
                at=r.at,
            )
            for r in rows
        ]

    # -- feedback / learning ----------------------------------------------
    def record_feedback(
        self, action_ref: str, incident_id: str, outcome: str, note: str = ""
    ) -> None:
        self.session.add(
            ActionFeedbackORM(
                action_ref=action_ref,
                incident_id=incident_id,
                outcome=outcome,
                note=note,
                created_at=datetime.utcnow(),
            )
        )
        self.session.commit()
