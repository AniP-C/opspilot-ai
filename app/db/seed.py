"""Populate the operational (PostgreSQL/SQLite) store from the synthetic dataset.

Idempotent: clears the operational tables then reloads them. Loads BOTH the live
demo incidents (status=open) and the historical incidents (status=resolved) into
the ``incidents`` table so the incident source can serve any of them.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app import dataset
from app.db.models import (
    DeploymentORM,
    IncidentORM,
    RunbookORM,
    ServiceDependencyORM,
    ServiceORM,
    TelemetrySnapshotORM,
)
from app.logging_config import get_logger
from app.timeutils import parse_ts

logger = get_logger(__name__)


def _clear(session: Session) -> None:
    for model in (
        TelemetrySnapshotORM,
        DeploymentORM,
        ServiceDependencyORM,
        IncidentORM,
        RunbookORM,
        ServiceORM,
    ):
        session.query(model).delete()


def seed_database(session: Session, data_dir: str | Path) -> dict[str, int]:
    """Load the whole dataset into the operational store. Returns row counts."""
    data_dir = Path(data_dir)
    _clear(session)

    # Services
    services = dataset.load_services(data_dir)
    for s in services:
        session.add(
            ServiceORM(
                id=s["id"],
                name=s.get("name", ""),
                description=s.get("description", ""),
                owner=s.get("owner", ""),
                criticality=s.get("criticality", ""),
                environment=s.get("environment", "production"),
            )
        )

    # Dependencies
    dependencies = dataset.load_dependencies(data_dir)
    for d in dependencies:
        session.add(
            ServiceDependencyORM(
                source_service=d["source_service"],
                target_service=d["target_service"],
                relationship=d.get("relationship", "calls"),
                protocol=d.get("protocol", ""),
                criticality=d.get("criticality", ""),
            )
        )

    # Deployments
    deployments = dataset.load_deployments(data_dir)
    for dep in deployments:
        session.add(
            DeploymentORM(
                id=dep["deployment_id"],
                service=dep["service"],
                version=dep.get("version", ""),
                environment=dep.get("environment", "production"),
                commit_id=dep.get("commit_id", ""),
                deployed_at=parse_ts(dep.get("deployed_at")),
                changed_components=list(dep.get("changed_components", [])),
                status=dep.get("deployment_status", ""),
            )
        )

    # Telemetry
    telemetry = dataset.load_telemetry(data_dir)
    for t in telemetry:
        session.add(
            TelemetrySnapshotORM(
                service=t["service"],
                timestamp=parse_ts(t.get("timestamp")),
                error_rate=float(t.get("error_rate_percent", 0.0) or 0.0),
                latency_ms=int(t.get("latency_ms", 0) or 0),
                throughput=int(t.get("throughput", 0) or 0),
                health_status=t.get("health_status", ""),
                active_connections=int(t.get("active_connections", 0) or 0),
                meta=dict(t.get("metadata", {}) or {}),
            )
        )

    # Live/demo incidents (status=open)
    demo = dataset.load_seed_incidents(data_dir)
    for inc in demo:
        session.add(
            IncidentORM(
                id=inc["incident_id"],
                external_id=inc["incident_id"],
                source="servicenow",
                title=inc.get("title", ""),
                description=inc.get("description", ""),
                service=inc.get("service", ""),
                severity=inc.get("severity", ""),
                environment=inc.get("environment", "production"),
                status="open",
                created_at=parse_ts(inc.get("created_at")),
                resolved_at=None,
                symptoms=list(inc.get("symptoms", [])),
            )
        )

    # Historical incidents (status=resolved)
    historical = dataset.load_historical_incidents(data_dir)
    for inc in historical:
        session.add(
            IncidentORM(
                id=inc["incident_id"],
                external_id=inc["incident_id"],
                source="servicenow",
                title=inc.get("title", ""),
                description=inc.get("description", ""),
                service=inc.get("service", ""),
                severity=inc.get("severity", ""),
                environment=inc.get("environment", "production"),
                status="resolved",
                created_at=parse_ts(inc.get("created_at")),
                resolved_at=parse_ts(inc.get("resolved_at")),
                symptoms=list(inc.get("symptoms", [])),
            )
        )

    # Runbooks (content + metadata; vectors live in Chroma, not here)
    runbooks = dataset.iter_runbooks(data_dir)
    for rb in runbooks:
        session.add(
            RunbookORM(
                id=rb["id"],
                title=rb["title"],
                content=rb["content"],
                applicable_services=rb["applicable_services"],
                document_type="runbook",
                source_path=rb["source_path"],
            )
        )

    session.commit()
    counts = {
        "services": len(services),
        "dependencies": len(dependencies),
        "deployments": len(deployments),
        "telemetry_snapshots": len(telemetry),
        "incidents": len(demo) + len(historical),
        "runbooks": len(runbooks),
    }
    logger.info("seed_database: %s", counts)
    return counts
