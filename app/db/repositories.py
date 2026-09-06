"""Repositories: the only place that maps ORM rows <-> domain models.

Each repository takes an active SQLAlchemy ``Session``. Callers (the context
builder, API dependencies, scripts) own the session lifecycle.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    DeploymentORM,
    IncidentEvidenceORM,
    IncidentORM,
    RunbookORM,
    ServiceDependencyORM,
    ServiceORM,
    TelemetrySnapshotORM,
)
from app.domain.models import (
    Deployment,
    Evidence,
    Incident,
    Service,
    ServiceDependency,
    TelemetrySnapshot,
)

# --------------------------------------------------------------------------- #
# ORM -> domain mappers
# --------------------------------------------------------------------------- #


def _to_service(row: ServiceORM) -> Service:
    return Service(
        id=row.id,
        name=row.name,
        description=row.description,
        owner=row.owner,
        criticality=row.criticality or "unknown",
        environment=row.environment or "production",
    )


def _to_dependency(row: ServiceDependencyORM) -> ServiceDependency:
    return ServiceDependency(
        source_service=row.source_service,
        target_service=row.target_service,
        relationship=row.relationship,
        protocol=row.protocol,
        criticality=row.criticality or "unknown",
    )


def _to_incident(row: IncidentORM) -> Incident:
    return Incident(
        id=row.id,
        external_id=row.external_id,
        source=row.source,
        title=row.title,
        description=row.description,
        service=row.service,
        severity=row.severity or "UNKNOWN",
        environment=row.environment or "production",
        status=row.status or "open",
        created_at=row.created_at,
        resolved_at=row.resolved_at,
        symptoms=list(row.symptoms or []),
    )


def _to_deployment(row: DeploymentORM) -> Deployment:
    return Deployment(
        id=row.id,
        service=row.service,
        version=row.version,
        environment=row.environment or "production",
        commit_id=row.commit_id,
        deployed_at=row.deployed_at,
        changed_components=list(row.changed_components or []),
        status=row.status or "unknown",
    )


def _to_telemetry(row: TelemetrySnapshotORM) -> TelemetrySnapshot:
    return TelemetrySnapshot(
        service=row.service,
        timestamp=row.timestamp,
        error_rate=row.error_rate,
        latency_ms=row.latency_ms,
        throughput=row.throughput,
        health_status=row.health_status or "unknown",
        active_connections=row.active_connections,
        metadata=dict(row.meta or {}),
    )


# --------------------------------------------------------------------------- #
# Repositories
# --------------------------------------------------------------------------- #


class ServiceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_service(self, service_id: str) -> Service | None:
        row = self.session.get(ServiceORM, service_id)
        return _to_service(row) if row else None

    def list_services(self) -> list[Service]:
        rows = self.session.scalars(select(ServiceORM).order_by(ServiceORM.id)).all()
        return [_to_service(r) for r in rows]


class TopologyRepository:
    """Raw edge access. Graph traversal lives in ``app.topology.service``."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def edges_from(self, service_id: str) -> list[ServiceDependency]:
        rows = self.session.scalars(
            select(ServiceDependencyORM)
            .where(ServiceDependencyORM.source_service == service_id)
            .order_by(ServiceDependencyORM.target_service)
        ).all()
        return [_to_dependency(r) for r in rows]

    def edges_into(self, service_id: str) -> list[ServiceDependency]:
        rows = self.session.scalars(
            select(ServiceDependencyORM)
            .where(ServiceDependencyORM.target_service == service_id)
            .order_by(ServiceDependencyORM.source_service)
        ).all()
        return [_to_dependency(r) for r in rows]

    def all_edges(self) -> list[ServiceDependency]:
        rows = self.session.scalars(
            select(ServiceDependencyORM).order_by(
                ServiceDependencyORM.source_service, ServiceDependencyORM.target_service
            )
        ).all()
        return [_to_dependency(r) for r in rows]


class IncidentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_incident(self, incident_id: str) -> Incident | None:
        row = self.session.get(IncidentORM, incident_id)
        return _to_incident(row) if row else None

    def list_incidents(self) -> list[Incident]:
        rows = self.session.scalars(select(IncidentORM).order_by(IncidentORM.id)).all()
        return [_to_incident(r) for r in rows]


class DeploymentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_recent(
        self,
        service: str,
        incident_ts: datetime | None,
        window_minutes: int = 60,
    ) -> list[Deployment]:
        """Deployments for ``service`` within ``[incident_ts - window, incident_ts]``.

        Phase 1 only reports *"these deployments happened recently"* — it makes
        no causation inference. If ``incident_ts`` is unknown, returns nothing.
        """
        if incident_ts is None:
            return []
        start = incident_ts - timedelta(minutes=window_minutes)
        rows = self.session.scalars(
            select(DeploymentORM)
            .where(
                DeploymentORM.service == service,
                DeploymentORM.deployed_at >= start,
                DeploymentORM.deployed_at <= incident_ts,
            )
            .order_by(DeploymentORM.deployed_at.desc(), DeploymentORM.id)
        ).all()
        return [_to_deployment(r) for r in rows]

    def get_recent_for_services(
        self,
        services: list[str],
        incident_ts: datetime | None,
        window_minutes: int = 60,
    ) -> list[Deployment]:
        """Recent deployments across several relevant services (deduped, sorted).

        Used for topology-aware deployment context (affected service + its
        dependencies). Observed context only — no causation is inferred.
        """
        if incident_ts is None or not services:
            return []
        start = incident_ts - timedelta(minutes=window_minutes)
        rows = self.session.scalars(
            select(DeploymentORM)
            .where(
                DeploymentORM.service.in_(list(dict.fromkeys(services))),
                DeploymentORM.deployed_at >= start,
                DeploymentORM.deployed_at <= incident_ts,
            )
            .order_by(DeploymentORM.deployed_at.desc(), DeploymentORM.id)
        ).all()
        return [_to_deployment(r) for r in rows]

    def list_for_service(self, service: str) -> list[Deployment]:
        rows = self.session.scalars(
            select(DeploymentORM)
            .where(DeploymentORM.service == service)
            .order_by(DeploymentORM.deployed_at.desc(), DeploymentORM.id)
        ).all()
        return [_to_deployment(r) for r in rows]


class TelemetryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_for_service(self, service: str) -> list[TelemetrySnapshot]:
        rows = self.session.scalars(
            select(TelemetrySnapshotORM)
            .where(TelemetrySnapshotORM.service == service)
            .order_by(TelemetrySnapshotORM.timestamp)
        ).all()
        return [_to_telemetry(r) for r in rows]

    def get_nearest(
        self, service: str, at: datetime | None
    ) -> TelemetrySnapshot | None:
        """Snapshot for ``service`` nearest ``at``, preferring the most recent
        reading at or before that time."""
        snapshots = self.list_for_service(service)
        if not snapshots:
            return None
        if at is None:
            return snapshots[-1]  # most recent overall
        with_ts = [s for s in snapshots if s.timestamp is not None]
        if not with_ts:
            return snapshots[-1]
        at_or_before = [s for s in with_ts if s.timestamp <= at]
        if at_or_before:
            return max(at_or_before, key=lambda s: s.timestamp)  # type: ignore[arg-type]
        # None before -> nearest after
        return min(with_ts, key=lambda s: abs((s.timestamp - at).total_seconds()))  # type: ignore[arg-type]


class RunbookRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, runbook_id: str) -> RunbookORM | None:
        return self.session.get(RunbookORM, runbook_id)

    def list_runbooks(self) -> list[RunbookORM]:
        return list(
            self.session.scalars(select(RunbookORM).order_by(RunbookORM.id)).all()
        )


class EvidenceRepository:
    """Persist / read the ``incident_evidence`` table."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def replace_for_incident(
        self, incident_id: str, evidences: list[Evidence]
    ) -> None:
        self.session.query(IncidentEvidenceORM).filter(
            IncidentEvidenceORM.incident_id == incident_id
        ).delete()
        now = datetime.utcnow()
        for ev in evidences:
            self.session.add(
                IncidentEvidenceORM(
                    incident_id=incident_id,
                    source=ev.source,
                    evidence_type=(
                        ev.evidence_type.value
                        if hasattr(ev.evidence_type, "value")
                        else str(ev.evidence_type)
                    ),
                    title=ev.title,
                    content=ev.content,
                    timestamp=ev.timestamp,
                    meta=ev.metadata,
                    created_at=now,
                )
            )
        self.session.commit()

    def list_for_incident(self, incident_id: str) -> list[Evidence]:
        rows = self.session.scalars(
            select(IncidentEvidenceORM)
            .where(IncidentEvidenceORM.incident_id == incident_id)
            .order_by(IncidentEvidenceORM.id)
        ).all()
        return [
            Evidence(
                source=r.source,
                evidence_type=r.evidence_type,
                title=r.title,
                content=r.content,
                timestamp=r.timestamp,
                metadata=dict(r.meta or {}),
            )
            for r in rows
        ]
