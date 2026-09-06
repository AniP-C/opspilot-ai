"""New Relic integration (telemetry source).

``MockNewRelicClient`` serves telemetry snapshots from the local store;
``NewRelicClient`` reads from the live New Relic API. Both satisfy the
``TelemetrySource`` protocol and return internal ``TelemetrySnapshot`` models.
"""

from app.integrations.newrelic.mock_client import MockNewRelicClient

__all__ = ["MockNewRelicClient"]
