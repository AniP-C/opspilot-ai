"""Audit trail helper.

Every important remediation event is appended (append-only) so the trail can
answer: who/what initiated this, what happened, why, which incident, which action,
which evidence, which policy, which approval, and what the result was.

Secrets are never placed in audit details — only structured, non-sensitive fields.
"""

from __future__ import annotations

from app.remediation.models import AuditEvent, AuditEventType
from app.remediation.store import RemediationStore


class AuditRecorder:
    def __init__(self, store: RemediationStore, incident_id: str) -> None:
        self._store = store
        self.incident_id = incident_id
        self.events: list[AuditEvent] = []

    def emit(
        self,
        event_type: AuditEventType,
        summary: str,
        *,
        action_ref: str | None = None,
        detail: dict | None = None,
        actor: str = "system",
    ) -> AuditEvent:
        event = AuditEvent(
            incident_id=self.incident_id,
            action_ref=action_ref,
            event_type=event_type,
            summary=summary,
            detail=detail or {},
            actor=actor,
        )
        stored = self._store.append_audit(event)
        self.events.append(stored)
        return stored
