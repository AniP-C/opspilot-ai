"""Controlled vocabularies used across the domain models.

All enums are string-valued so they serialize cleanly to JSON and compare with
plain strings. ``_missing_`` is overridden to be lenient: an unrecognised value
maps to a well-defined ``UNKNOWN`` member (where present) rather than raising,
so a single odd record from an upstream source never crashes context building.
"""

from __future__ import annotations

from enum import Enum


class _LenientStrEnum(str, Enum):
    """Base for string enums that fall back to ``UNKNOWN`` on unknown input."""

    @classmethod
    def _missing_(cls, value: object):  # type: ignore[override]
        if isinstance(value, str):
            # case-insensitive match
            for member in cls:
                if member.value.lower() == value.lower():
                    return member
        return cls.__members__.get("UNKNOWN")


class Severity(_LenientStrEnum):
    SEV1 = "SEV1"
    SEV2 = "SEV2"
    SEV3 = "SEV3"
    SEV4 = "SEV4"
    UNKNOWN = "UNKNOWN"


class Environment(_LenientStrEnum):
    PRODUCTION = "production"
    STAGING = "staging"
    DEVELOPMENT = "development"
    UNKNOWN = "unknown"


class IncidentStatus(_LenientStrEnum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class Criticality(_LenientStrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class HealthStatus(_LenientStrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class DeploymentStatus(_LenientStrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    UNKNOWN = "unknown"


class EvidenceType(str, Enum):
    HISTORICAL_INCIDENT = "historical_incident"
    RUNBOOK = "runbook"
