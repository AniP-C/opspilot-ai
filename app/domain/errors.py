"""Domain-level exceptions.

These let the orchestration and API layers distinguish "the incident does not
exist" (a 404) from "the incident source is unreachable" (a 502), and to degrade
gracefully when an *optional* context source fails.
"""

from __future__ import annotations


class OpsPilotError(Exception):
    """Base class for all OpsPilot domain errors."""


class IncidentNotFound(OpsPilotError):
    """The requested incident id does not exist in the incident source."""

    def __init__(self, incident_id: str) -> None:
        self.incident_id = incident_id
        super().__init__(f"Incident '{incident_id}' not found")


class IncidentSourceUnavailable(OpsPilotError):
    """The incident source (e.g. ServiceNow) could not be reached.

    The incident is the one mandatory input to context building — if it cannot
    be fetched we must fail loudly and never fabricate an incident.
    """

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"Incident source unavailable: {detail}")


class ServiceNotFound(OpsPilotError):
    """The requested service id does not exist in the topology store."""

    def __init__(self, service_id: str) -> None:
        self.service_id = service_id
        super().__init__(f"Service '{service_id}' not found")


class TelemetryUnavailable(OpsPilotError):
    """Telemetry could not be fetched — non-fatal for context building."""


class RetrievalError(OpsPilotError):
    """A semantic retrieval operation failed — non-fatal for context building."""
