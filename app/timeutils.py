"""Datetime helpers.

We standardise on **naive UTC** datetimes everywhere in the domain. This keeps
comparisons (e.g. deployment time-window filtering) consistent across SQLite and
PostgreSQL, both of which return naive datetimes for our columns.
"""

from __future__ import annotations

from datetime import datetime, timezone


def parse_ts(value: object) -> datetime | None:
    """Parse an ISO-8601 string (``...Z`` accepted) into a naive UTC datetime.

    Returns ``None`` for empty/unparseable input rather than raising, so a single
    malformed timestamp never crashes ingestion.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return _to_naive_utc(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    # datetime.fromisoformat (3.11+) accepts a trailing 'Z'.
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    return _to_naive_utc(dt)


def _to_naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def to_iso(dt: datetime | None) -> str | None:
    """Render a naive UTC datetime as an ISO-8601 string with a ``Z`` suffix."""
    if dt is None:
        return None
    return dt.replace(microsecond=0).isoformat() + "Z"
