"""ServiceNow integration (incident source).

``MockServiceNowClient`` serves incidents from the local operational store;
``ServiceNowClient`` reads from a live ServiceNow instance. Both satisfy the
``IncidentSource`` protocol and return the internal ``Incident`` model.
Phase 1 needs READ capability only — no incident write-back.
"""

from app.integrations.servicenow.mock_client import (
    MockServiceNowClient,
    MockServiceNowWriter,
)

__all__ = ["MockServiceNowClient", "MockServiceNowWriter"]
