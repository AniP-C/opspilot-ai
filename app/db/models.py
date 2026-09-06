"""SQLAlchemy ORM models (operational state).

Tables: services, service_dependencies, incidents, deployments,
telemetry_snapshots, incident_evidence, runbooks.

Note: the attribute for a JSON "metadata" column is named ``meta`` because
``metadata`` is reserved on the declarative Base.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ServiceORM(Base):
    __tablename__ = "services"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    owner: Mapped[str] = mapped_column(String(200), default="")
    criticality: Mapped[str] = mapped_column(String(32), default="")
    environment: Mapped[str] = mapped_column(String(32), default="production")


class ServiceDependencyORM(Base):
    __tablename__ = "service_dependencies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_service: Mapped[str] = mapped_column(String(64), index=True)
    target_service: Mapped[str] = mapped_column(String(64), index=True)
    relationship: Mapped[str] = mapped_column(String(64), default="calls")
    protocol: Mapped[str] = mapped_column(String(32), default="")
    criticality: Mapped[str] = mapped_column(String(32), default="")


class IncidentORM(Base):
    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="servicenow")
    title: Mapped[str] = mapped_column(String(400), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    service: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16), default="")
    environment: Mapped[str] = mapped_column(String(32), default="production")
    status: Mapped[str] = mapped_column(String(32), default="open")
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    symptoms: Mapped[list] = mapped_column(JSON, default=list)


class DeploymentORM(Base):
    __tablename__ = "deployments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    service: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[str] = mapped_column(String(64), default="")
    environment: Mapped[str] = mapped_column(String(32), default="production")
    commit_id: Mapped[str] = mapped_column(String(64), default="")
    deployed_at: Mapped[datetime | None] = mapped_column(DateTime, index=True, nullable=True)
    changed_components: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="")


class TelemetrySnapshotORM(Base):
    __tablename__ = "telemetry_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service: Mapped[str] = mapped_column(String(64), index=True)
    timestamp: Mapped[datetime | None] = mapped_column(DateTime, index=True, nullable=True)
    error_rate: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    throughput: Mapped[int] = mapped_column(Integer, default=0)
    health_status: Mapped[str] = mapped_column(String(32), default="")
    active_connections: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[dict] = mapped_column("metadata", JSON, default=dict)


class IncidentEvidenceORM(Base):
    __tablename__ = "incident_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(64), default="")
    evidence_type: Mapped[str] = mapped_column(String(64), default="")
    title: Mapped[str] = mapped_column(String(400), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    timestamp: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    meta: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RunbookORM(Base):
    __tablename__ = "runbooks"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    applicable_services: Mapped[list] = mapped_column(JSON, default=list)
    document_type: Mapped[str] = mapped_column(String(32), default="runbook")
    source_path: Mapped[str] = mapped_column(String(400), default="")


class InvestigationORM(Base):
    """Persisted Phase 2 investigations (enables the human-in-the-loop resume)."""

    __tablename__ = "investigations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # investigation_id
    incident_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="")
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    human_facts: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# --------------------------------------------------------------------------- #
# Phase 3: remediation state (all important transitions are persisted)
# --------------------------------------------------------------------------- #
class RemediationActionORM(Base):
    """One remediation action + its whole lifecycle (proposal, policy, approval,
    execution, verification), denormalised as JSON for convenient reads. Child
    tables below record the individual state transitions and enable idempotency."""

    __tablename__ = "remediation_actions"

    action_ref: Mapped[str] = mapped_column(String(160), primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(64), index=True)
    investigation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="")
    proposal: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    policy: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    approval: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    execution: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    verification: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    servicenow_updated: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ActionApprovalORM(Base):
    """A persisted human approval decision (real state transition, not a UI toggle)."""

    __tablename__ = "action_approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action_ref: Mapped[str] = mapped_column(String(160), index=True)
    decision: Mapped[str] = mapped_column(String(32), default="")
    approver: Mapped[str] = mapped_column(String(120), default="operator")
    note: Mapped[str] = mapped_column(Text, default="")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ActionExecutionORM(Base):
    """A persisted execution attempt. ``idempotency_key`` is unique so a retry /
    duplicate request cannot execute the same action twice."""

    __tablename__ = "action_executions"

    execution_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    action_ref: Mapped[str] = mapped_column(String(160), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    mode: Mapped[str] = mapped_column(String(16), default="mock")
    status: Mapped[str] = mapped_column(String(32), default="")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class VerificationResultORM(Base):
    __tablename__ = "verification_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action_ref: Mapped[str] = mapped_column(String(160), index=True)
    execution_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="")
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AuditEventORM(Base):
    """Append-only audit trail for every important remediation event."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[str] = mapped_column(String(64), index=True)
    action_ref: Mapped[str | None] = mapped_column(String(160), index=True, nullable=True)
    event_type: Mapped[str] = mapped_column(String(48), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    actor: Mapped[str] = mapped_column(String(120), default="system")
    at: Mapped[datetime | None] = mapped_column(DateTime, index=True, nullable=True)


class ActionFeedbackORM(Base):
    """Learning/feedback signal recorded after a remediation completes."""

    __tablename__ = "action_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action_ref: Mapped[str] = mapped_column(String(160), index=True)
    incident_id: Mapped[str] = mapped_column(String(64), index=True)
    outcome: Mapped[str] = mapped_column(String(32), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
