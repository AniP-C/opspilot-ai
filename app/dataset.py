"""Dataset access: the single place that knows the on-disk synthetic data layout.

Reads the ``ops-pilot-demo-data`` files (topology, incidents, deployments,
telemetry, runbooks). Both the DB seeder and the Chroma ingester use these
helpers, so file-format knowledge lives in exactly one module.

Note: seed incidents carry a hidden ``ground_truth`` validation payload. It is
deliberately dropped here and never loaded into the operational store — Phase 1
must not expose it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_RUNBOOK_SVC_RE = re.compile(r"svc-[a-z0-9-]+")


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_services(data_dir: Path) -> list[dict]:
    return _load_json(data_dir / "topology" / "services.json").get("services", [])


def load_dependencies(data_dir: Path) -> list[dict]:
    return _load_json(data_dir / "topology" / "dependencies.json").get("dependencies", [])


def load_deployments(data_dir: Path) -> list[dict]:
    return _load_json(data_dir / "deployments" / "deployments.json").get("deployments", [])


def load_telemetry(data_dir: Path) -> list[dict]:
    return _load_json(data_dir / "telemetry" / "telemetry.json").get("telemetry", [])


def load_seed_incidents(data_dir: Path) -> list[dict]:
    """Return the live/demo incident payloads (``ground_truth`` stripped)."""
    raw = _load_json(data_dir / "incidents" / "seed_incidents.json").get(
        "seed_incidents", []
    )
    incidents: list[dict] = []
    for entry in raw:
        incident = dict(entry.get("incident", {}))
        incident.pop("ground_truth", None)  # defensive
        incidents.append(incident)
    return incidents


def load_historical_incidents(data_dir: Path) -> list[dict]:
    return _load_json(data_dir / "incidents" / "historical_incidents.json").get(
        "historical_incidents", []
    )


def parse_runbook_markdown(text: str, filename: str) -> dict:
    """Extract ``{id, title, content, applicable_services, source_path}``."""
    title = ""
    applicable: list[str] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not title and stripped.startswith("# "):
            title = stripped[2:].strip()
        if stripped.lower().startswith("## applicable services"):
            # Collect following non-empty lines until the next section header.
            for follow in lines[i + 1 :]:
                fs = follow.strip()
                if fs.startswith("## "):
                    break
                if fs:
                    applicable.extend(_RUNBOOK_SVC_RE.findall(fs))
    stem = Path(filename).stem
    return {
        "id": stem,
        "title": title or stem.replace("-", " ").title(),
        "content": text,
        "applicable_services": sorted(dict.fromkeys(applicable)),  # dedupe, keep stable
        "source_path": filename,
    }


def iter_runbooks(data_dir: Path) -> list[dict]:
    runbook_dir = data_dir / "runbooks"
    runbooks: list[dict] = []
    for path in sorted(runbook_dir.glob("*.md")):
        runbooks.append(parse_runbook_markdown(path.read_text(encoding="utf-8"), path.name))
    return runbooks
