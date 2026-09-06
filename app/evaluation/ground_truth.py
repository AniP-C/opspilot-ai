"""Load the hidden ground truth for evaluation (TEST/EVAL ONLY).

Reads ``seed_incidents.json`` *with* its ``ground_truth`` payload — the exact
payload the runtime deliberately drops. Never import this from runtime code.
"""

from __future__ import annotations

import json
from pathlib import Path


def load_ground_truth(data_dir: str | Path) -> dict[str, dict]:
    """Return ``{incident_id: ground_truth}`` for the demo incidents."""
    path = Path(data_dir) / "incidents" / "seed_incidents.json"
    raw = json.loads(path.read_text(encoding="utf-8")).get("seed_incidents", [])
    out: dict[str, dict] = {}
    for entry in raw:
        incident = entry.get("incident", {})
        gt = entry.get("ground_truth", {}) or {}
        out[incident.get("incident_id")] = gt
    return out


def expected_correlation(gt: dict) -> str | None:
    """Interpret ground truth into an expected deployment-correlation class.

    Returns "true" (should be HIGH/MEDIUM), "false" (should be LOW/NONE), or
    None (not applicable).
    """
    if str(gt.get("correlation", "")).lower() == "false":
        return "false"
    if gt.get("related_deployment_id") or gt.get("expected_related_deployment"):
        return "true"
    return None
