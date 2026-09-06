"""Mock New Relic client.

Serves telemetry snapshots from the local ``telemetry_snapshots`` store, picking
the reading nearest the incident time. No credentials or network required.
"""

from __future__ import annotations

from datetime import datetime

from app.db.repositories import TelemetryRepository
from app.domain.models import TelemetrySnapshot
from app.logging_config import get_logger

logger = get_logger(__name__)


class MockNewRelicClient:
    def __init__(self, telemetry_repo: TelemetryRepository) -> None:
        self._repo = telemetry_repo

    def get_service_health(
        self, service: str, at: datetime | None = None
    ) -> TelemetrySnapshot | None:
        snapshot = self._repo.get_nearest(service, at)
        if snapshot is None:
            logger.info("telemetry_lookup: no snapshot for service=%s (mock)", service)
            return None
        logger.info(
            "telemetry_lookup: service=%s health=%s error_rate=%.2f latency_ms=%s (mock)",
            service,
            snapshot.health_status.value,
            snapshot.error_rate,
            snapshot.latency_ms,
        )
        return snapshot

    def get_error_rate(self, service: str, at: datetime | None = None) -> float | None:
        snap = self.get_service_health(service, at)
        return snap.error_rate if snap else None

    def get_latency(self, service: str, at: datetime | None = None) -> int | None:
        snap = self.get_service_health(service, at)
        return snap.latency_ms if snap else None

    def get_recent_errors(self, service: str, at: datetime | None = None) -> list[str]:
        # The synthetic dataset carries no raw error-log stream; return an empty
        # list rather than fabricating error messages.
        return []
