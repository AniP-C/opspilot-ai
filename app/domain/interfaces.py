"""Abstract interfaces (Protocols) for pluggable infrastructure.

The core application depends on these abstractions, never on a concrete client.
Both mock and real implementations satisfy the same Protocol, so the system can
run fully locally (mock) or against live systems (real) without any change to
the orchestration layer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from app.domain.models import Evidence, Incident, TelemetrySnapshot


@runtime_checkable
class IncidentSource(Protocol):
    """Read access to incidents (e.g. ServiceNow)."""

    def get_incident(self, incident_id: str) -> Incident:
        """Return the incident, or raise ``IncidentNotFound`` /
        ``IncidentSourceUnavailable``. Must never fabricate an incident."""
        ...


@runtime_checkable
class IncidentWriter(Protocol):
    """Write-back to the incident system of record (e.g. ServiceNow work notes).

    Phase 1 was read-only; Phase 3 adds write-back through this boundary so the
    core never depends on ServiceNow's field names. Both a mock and a (gated)
    real implementation satisfy it. Returns a small receipt dict; must never log
    credentials."""

    def add_work_note(self, incident_id: str, note: str) -> dict:
        """Append a work note / RCA entry to the incident. Returns a receipt."""
        ...

    def update_state(self, incident_id: str, state: str) -> dict:
        """Update the incident state/status. Returns a receipt."""
        ...


@runtime_checkable
class TelemetrySource(Protocol):
    """Read access to telemetry (e.g. New Relic)."""

    def get_service_health(
        self, service: str, at: datetime | None = None
    ) -> TelemetrySnapshot | None:
        """Return the telemetry snapshot for ``service`` at/nearest ``at``."""
        ...

    def get_error_rate(self, service: str, at: datetime | None = None) -> float | None:
        ...

    def get_latency(self, service: str, at: datetime | None = None) -> int | None:
        ...

    def get_recent_errors(
        self, service: str, at: datetime | None = None
    ) -> list[str]:
        ...


@runtime_checkable
class HistoricalIncidentRetriever(Protocol):
    """Semantic retrieval of similar historical incidents."""

    def search(
        self,
        query: str,
        *,
        where: dict | None = None,
        top_k: int = 5,
    ) -> list[Evidence]:
        ...


@runtime_checkable
class RunbookRetriever(Protocol):
    """Semantic retrieval of relevant runbooks."""

    def search(
        self,
        query: str,
        *,
        where: dict | None = None,
        top_k: int = 3,
    ) -> list[Evidence]:
        ...
