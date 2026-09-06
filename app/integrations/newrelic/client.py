"""Real New Relic client.

Structured for future activation against the New Relic NerdGraph API. Reads
credentials from configuration and maps responses via ``newrelic_to_snapshot``.
Isolated here so the core never depends on New Relic directly.
"""

from __future__ import annotations

from datetime import datetime

import httpx

from app.domain.errors import TelemetryUnavailable
from app.domain.models import TelemetrySnapshot
from app.integrations.newrelic.adapter import newrelic_to_snapshot
from app.logging_config import get_logger

logger = get_logger(__name__)

_NERDGRAPH_URL = "https://api.newrelic.com/graphql"


class NewRelicClient:
    def __init__(
        self,
        api_key: str | None,
        account_id: str | None,
        *,
        http_client: httpx.Client | None = None,
        timeout: float = 10.0,
        nerdgraph_url: str = _NERDGRAPH_URL,
    ) -> None:
        if not api_key or not account_id:
            raise TelemetryUnavailable(
                "New Relic real mode requires NEWRELIC_API_KEY and NEWRELIC_ACCOUNT_ID"
            )
        self._api_key = api_key
        self._account_id = account_id
        self._timeout = timeout
        self._client = http_client
        self._url = nerdgraph_url

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "API-Key": self._api_key}

    def _nrql(self, nrql: str) -> list[dict]:
        query = {
            "query": (
                "{ actor { account(id: %s) { nrql(query: %r) { results } } } }"
                % (self._account_id, nrql)
            )
        }
        try:
            client = self._client or httpx.Client(timeout=self._timeout)
            close = self._client is None
            try:
                resp = client.post(self._url, json=query, headers=self._headers())
            finally:
                if close:
                    client.close()
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise TelemetryUnavailable(f"New Relic request failed: {exc}") from exc
        try:
            return resp.json()["data"]["actor"]["account"]["nrql"]["results"]
        except (KeyError, TypeError) as exc:
            raise TelemetryUnavailable(f"Unexpected New Relic response: {exc}") from exc

    def get_service_health(
        self, service: str, at: datetime | None = None
    ) -> TelemetrySnapshot | None:
        logger.info("telemetry_lookup: querying New Relic for service=%s (real)", service)
        nrql = (
            "SELECT percentage(count(*), WHERE error IS true) AS error_rate, "
            "percentile(duration, 99) AS latency_ms, rate(count(*), 1 second) AS throughput "
            f"FROM Transaction WHERE appName = '{service}' SINCE 5 minutes ago"
        )
        results = self._nrql(nrql)
        if not results:
            return None
        return newrelic_to_snapshot(service, results[0])

    def get_error_rate(self, service: str, at: datetime | None = None) -> float | None:
        snap = self.get_service_health(service, at)
        return snap.error_rate if snap else None

    def get_latency(self, service: str, at: datetime | None = None) -> int | None:
        snap = self.get_service_health(service, at)
        return snap.latency_ms if snap else None

    def get_recent_errors(self, service: str, at: datetime | None = None) -> list[str]:
        nrql = (
            "SELECT latest(error.message) AS message FROM TransactionError "
            f"WHERE appName = '{service}' SINCE 15 minutes ago LIMIT 20"
        )
        try:
            results = self._nrql(nrql)
        except TelemetryUnavailable:
            return []
        return [str(r.get("message", "")) for r in results if r.get("message")]
