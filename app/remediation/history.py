"""Recurrence + historical-success derivation (observable history ONLY).

The policy engine needs to know "how often has this failure pattern happened, and
how often did remediation succeed?". We derive that from the *recorded* historical
incident data (``incident_fingerprint`` + ``resolution_success`` — the same
observable fields Phase 1 already ingests into Chroma and displays in the UI).

We deliberately do NOT read the hidden ``ground_truth`` payload of the demo
incidents. Every number returned here comes from stored, recorded resolution
information — never fabricated, never from a leaked label.

The fingerprint -> hypothesis-kind mapping is REUSED from Phase 2
(``app.investigation.analysis.FINGERPRINT_KIND``) so there is one source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app import dataset
from app.investigation.analysis import FINGERPRINT_KIND

# Inverse of FINGERPRINT_KIND: hypothesis kind -> the fingerprints that map to it.
_KIND_TO_FINGERPRINTS: dict[str, set[str]] = {}
for _fp, _kind in FINGERPRINT_KIND.items():
    _KIND_TO_FINGERPRINTS.setdefault(_kind, set()).add(_fp)


@dataclass(frozen=True)
class Precedent:
    """Observable precedent for a (kind, service) pattern."""

    recurrence_count: int          # how many times this pattern was recorded on the service
    successes: int                 # of those, how many had a recorded successful resolution
    total: int                     # == recurrence_count (kept explicit for clarity)
    example_resolutions: tuple[str, ...] = ()

    @property
    def success_rate(self) -> float | None:
        if self.total <= 0:
            return None
        return round(self.successes / self.total, 3)


class HistoricalStats:
    """Precomputed, deterministic view over recorded historical incidents."""

    def __init__(self, historical: list[dict]) -> None:
        self._historical = historical

    @classmethod
    def from_data_dir(cls, data_dir: str | Path) -> "HistoricalStats":
        return cls(dataset.load_historical_incidents(Path(data_dir)))

    def precedent(self, kind: str | None, service: str | None) -> Precedent:
        """Occurrences + successful resolutions of ``kind`` recorded on ``service``.

        A ``kind`` with no known fingerprint mapping (e.g. ``data_integrity``,
        ``network_infrastructure``, ``novel_unknown``) yields zero precedent — the
        pattern is genuinely novel to our recorded history, which the policy engine
        treats as a reason NOT to auto-remediate.
        """
        fingerprints = _KIND_TO_FINGERPRINTS.get(kind or "", set())
        if not fingerprints:
            return Precedent(0, 0, 0, ())

        matches = [
            h
            for h in self._historical
            if h.get("incident_fingerprint") in fingerprints
            and (service is None or h.get("service") == service)
        ]
        successes = sum(1 for h in matches if bool(h.get("resolution_success")))
        resolutions = tuple(
            str(h.get("resolution", "")).strip()
            for h in matches
            if h.get("resolution")
        )[:5]
        return Precedent(
            recurrence_count=len(matches),
            successes=successes,
            total=len(matches),
            example_resolutions=resolutions,
        )
