"""Mock ServiceNow client.

Simulates the ServiceNow incident API by serving persisted fixtures from the
local operational store (the ``incidents`` table). Requires no credentials and
no network — but behaves like the real client from the caller's perspective:
same interface, same ``Incident`` return type, same "never fabricate" contract.
"""

from __future__ import annotations

from app.db.repositories import IncidentRepository
from app.domain.errors import IncidentNotFound
from app.domain.models import Incident
from app.logging_config import get_logger

logger = get_logger(__name__)


class MockServiceNowClient:
    def __init__(self, incident_repo: IncidentRepository) -> None:
        self._repo = incident_repo

    def get_incident(self, incident_id: str) -> Incident:
        incident = self._repo.get_incident(incident_id)
        if incident is None:
            logger.warning("incident_retrieval: '%s' not found (mock)", incident_id)
            raise IncidentNotFound(incident_id)
        logger.info(
            "incident_retrieval: '%s' service=%s severity=%s (mock)",
            incident.id,
            incident.service,
            incident.severity.value,
        )
        return incident


class MockServiceNowWriter:
    """Mock ServiceNow write-back. Simulates work-note / state updates without any
    network or credentials — records them in-memory and logs a receipt. Safe by
    default and used for the whole demo (real write-back is disabled unless
    explicitly configured)."""

    def __init__(self) -> None:
        # In-memory receipt log (a real instance would hit the Table API).
        self.work_notes: list[dict] = []
        self.state_changes: list[dict] = []

    def add_work_note(self, incident_id: str, note: str) -> dict:
        entry = {"incident_id": incident_id, "op": "work_note", "note": note, "mode": "mock"}
        self.work_notes.append(entry)
        logger.info("servicenow_write: work note added to '%s' (mock)", incident_id)
        return {"ok": True, **entry}

    def update_state(self, incident_id: str, state: str) -> dict:
        entry = {"incident_id": incident_id, "op": "state", "state": state, "mode": "mock"}
        self.state_changes.append(entry)
        logger.info("servicenow_write: state of '%s' -> %s (mock)", incident_id, state)
        return {"ok": True, **entry}
