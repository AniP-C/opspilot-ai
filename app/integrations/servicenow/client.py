"""Real ServiceNow client (READ only).

Exists so the system can be pointed at a live ServiceNow instance in a later
activation without touching the core. It reads credentials from configuration
(never hard-coded) and maps responses via ``servicenow_to_incident``.

Phase 1 does NOT implement incident write-back.
"""

from __future__ import annotations

import httpx

from app.domain.errors import IncidentNotFound, IncidentSourceUnavailable
from app.domain.models import Incident
from app.integrations.servicenow.adapter import servicenow_to_incident
from app.logging_config import get_logger

logger = get_logger(__name__)


class ServiceNowClient:
    def __init__(
        self,
        base_url: str | None,
        username: str | None,
        token: str | None,
        *,
        http_client: httpx.Client | None = None,
        timeout: float = 10.0,
    ) -> None:
        if not base_url or not token:
            # Fail loudly rather than silently degrade — the incident is the one
            # mandatory input and must never be fabricated.
            raise IncidentSourceUnavailable(
                "ServiceNow real mode requires SERVICENOW_URL and SERVICENOW_TOKEN"
            )
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._token = token
        self._timeout = timeout
        self._client = http_client

    def _headers(self) -> dict[str, str]:
        # Token/credentials are only ever placed in headers, never logged.
        return {"Accept": "application/json", "Authorization": f"Bearer {self._token}"}

    def get_incident(self, incident_id: str) -> Incident:
        url = f"{self._base_url}/api/now/table/incident"
        params = {"sysparm_query": f"number={incident_id}", "sysparm_limit": "1"}
        logger.info("incident_retrieval: querying ServiceNow for '%s' (real)", incident_id)
        try:
            client = self._client or httpx.Client(timeout=self._timeout)
            close = self._client is None
            try:
                resp = client.get(url, params=params, headers=self._headers())
            finally:
                if close:
                    client.close()
            resp.raise_for_status()
        except httpx.HTTPError as exc:  # network / status errors
            raise IncidentSourceUnavailable(f"ServiceNow request failed: {exc}") from exc

        results = resp.json().get("result", [])
        if not results:
            raise IncidentNotFound(incident_id)
        return servicenow_to_incident(results[0])


class ServiceNowWriter:
    """Real ServiceNow write-back (DISABLED by default).

    Uses the stable ServiceNow Table API: work notes and state are written by
    ``PATCH /api/now/table/incident/{sys_id}`` with a JSON body such as
    ``{"work_notes": "..."}`` or ``{"state": "6"}`` (``work_notes`` is a journal
    field, so a PATCH appends a new entry). The record is addressed by ``sys_id``,
    which we resolve from the incident number first.

    NOTE: verify the current Table API reference for your ServiceNow release before
    enabling real write-back. This is only constructed when BOTH
    ``SERVICENOW_MODE=real`` and ``SERVICENOW_WRITE_ENABLED=true`` — otherwise the
    safe mock writer is used. Credentials live only in headers and are never logged.
    """

    def __init__(
        self,
        base_url: str | None,
        username: str | None,
        token: str | None,
        *,
        http_client: httpx.Client | None = None,
        timeout: float = 10.0,
    ) -> None:
        if not base_url or not token:
            raise IncidentSourceUnavailable(
                "ServiceNow real write-back requires SERVICENOW_URL and SERVICENOW_TOKEN"
            )
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._token = token
        self._timeout = timeout
        self._client = http_client

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._token}",
        }

    def _resolve_sys_id(self, incident_id: str, client: httpx.Client) -> str:
        url = f"{self._base_url}/api/now/table/incident"
        params = {"sysparm_query": f"number={incident_id}", "sysparm_fields": "sys_id", "sysparm_limit": "1"}
        resp = client.get(url, params=params, headers=self._headers())
        resp.raise_for_status()
        results = resp.json().get("result", [])
        if not results:
            raise IncidentNotFound(incident_id)
        return results[0]["sys_id"]

    def _patch(self, incident_id: str, body: dict) -> dict:
        try:
            client = self._client or httpx.Client(timeout=self._timeout)
            close = self._client is None
            try:
                sys_id = self._resolve_sys_id(incident_id, client)
                url = f"{self._base_url}/api/now/table/incident/{sys_id}"
                resp = client.patch(url, json=body, headers=self._headers())
                resp.raise_for_status()
            finally:
                if close:
                    client.close()
        except httpx.HTTPError as exc:
            raise IncidentSourceUnavailable(f"ServiceNow write failed: {exc}") from exc
        logger.info("servicenow_write: patched '%s' fields=%s (real)", incident_id, list(body))
        return {"ok": True, "incident_id": incident_id, "mode": "real"}

    def add_work_note(self, incident_id: str, note: str) -> dict:
        return {**self._patch(incident_id, {"work_notes": note}), "op": "work_note"}

    def update_state(self, incident_id: str, state: str) -> dict:
        return {**self._patch(incident_id, {"state": state}), "op": "state"}
