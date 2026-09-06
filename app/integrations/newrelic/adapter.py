"""Adapter: New Relic NRQL result -> internal ``TelemetrySnapshot`` model."""

from __future__ import annotations

from typing import Any

from app.domain.models import TelemetrySnapshot
from app.timeutils import parse_ts


def _health_from(error_rate: float, latency_ms: int) -> str:
    """Derive a coarse health status when New Relic doesn't supply one."""
    if error_rate >= 10 or latency_ms >= 3000:
        return "critical"
    if error_rate >= 1 or latency_ms >= 1000:
        return "degraded"
    return "healthy"


def newrelic_to_snapshot(service: str, record: dict[str, Any]) -> TelemetrySnapshot:
    """Map a single NRQL result row to a telemetry snapshot.

    Accepts the common metric aliases produced by an NRQL query such as::

        SELECT percentage(count(*), WHERE error) AS error_rate,
               percentile(duration, 99) AS latency_ms, rate(count(*), 1 second)
        FROM Transaction WHERE appName = '<service>'
    """
    error_rate = float(record.get("error_rate", record.get("errorRate", 0.0)) or 0.0)
    latency_ms = int(record.get("latency_ms", record.get("latency", 0)) or 0)
    health = record.get("health_status") or _health_from(error_rate, latency_ms)
    return TelemetrySnapshot(
        service=service,
        timestamp=parse_ts(record.get("timestamp")),
        error_rate=error_rate,
        latency_ms=latency_ms,
        throughput=int(record.get("throughput", 0) or 0),
        health_status=health,
        active_connections=int(record.get("active_connections", 0) or 0),
        metadata=dict(record.get("metadata", {}) or {}),
    )
