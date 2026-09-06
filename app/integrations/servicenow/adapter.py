"""Adapter: ServiceNow incident payload -> internal ``Incident`` model.

This is the boundary that keeps the core application ignorant of ServiceNow's
field names and encodings. Only this module knows what ``short_description`` or
state code ``6`` mean.
"""

from __future__ import annotations

from typing import Any

from app.domain.models import Incident
from app.timeutils import parse_ts

# ServiceNow numeric state codes -> our incident status vocabulary.
_STATE_MAP = {
    "1": "open",          # New
    "2": "investigating",  # In Progress
    "3": "investigating",  # On Hold
    "6": "resolved",       # Resolved
    "7": "closed",         # Closed
}

# ServiceNow severity codes -> our SEV vocabulary.
_SEVERITY_MAP = {"1": "SEV1", "2": "SEV2", "3": "SEV3"}


def servicenow_to_incident(record: dict[str, Any]) -> Incident:
    """Map a single ServiceNow ``incident`` table record."""
    state = str(record.get("state", "")).strip()
    severity = str(record.get("severity", "")).strip()
    return Incident(
        id=str(record.get("number") or record.get("sys_id") or "").strip(),
        external_id=str(record.get("sys_id") or "") or None,
        source="servicenow",
        title=record.get("short_description", ""),
        description=record.get("description", ""),
        service=(record.get("business_service") or record.get("cmdb_ci") or ""),
        severity=_SEVERITY_MAP.get(severity, "UNKNOWN"),
        environment=record.get("u_environment", "production") or "production",
        status=_STATE_MAP.get(state, "open"),
        created_at=parse_ts(record.get("opened_at")),
        resolved_at=parse_ts(record.get("resolved_at")),
    )
