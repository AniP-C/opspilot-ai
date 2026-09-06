"""Internal domain models (Pydantic v2).

These are the clean, vendor-neutral models the core application speaks. Adapters
translate ServiceNow / New Relic / Chroma payloads into these types so nothing
downstream depends on an external response format.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import (
    Criticality,
    DeploymentStatus,
    Environment,
    EvidenceType,
    HealthStatus,
    IncidentStatus,
    Severity,
)


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Incident(_Base):
    """An operational incident (vendor-neutral)."""

    id: str
    external_id: str | None = None
    source: str = "servicenow"
    title: str
    description: str = ""
    service: str
    severity: Severity = Severity.UNKNOWN
    environment: Environment = Environment.PRODUCTION
    status: IncidentStatus = IncidentStatus.OPEN
    created_at: datetime | None = None
    resolved_at: datetime | None = None
    # Additive, optional context: observable symptoms reported with the incident.
    symptoms: list[str] = Field(default_factory=list)


class Service(_Base):
    """A service in the topology."""

    id: str
    name: str
    description: str = ""
    owner: str = ""
    criticality: Criticality = Criticality.UNKNOWN
    environment: Environment = Environment.PRODUCTION


class ServiceDependency(_Base):
    """A directed dependency edge: ``source_service`` -> ``target_service``."""

    source_service: str
    target_service: str
    relationship: str = "calls"
    protocol: str = ""
    criticality: Criticality = Criticality.UNKNOWN


class Deployment(_Base):
    """A CI/CD deployment record."""

    id: str
    service: str
    version: str = ""
    environment: Environment = Environment.PRODUCTION
    commit_id: str = ""
    deployed_at: datetime | None = None
    changed_components: list[str] = Field(default_factory=list)
    status: DeploymentStatus = DeploymentStatus.UNKNOWN


class TelemetrySnapshot(_Base):
    """A point-in-time telemetry reading for a service."""

    service: str
    timestamp: datetime | None = None
    error_rate: float = 0.0
    latency_ms: int = 0
    throughput: int = 0
    health_status: HealthStatus = HealthStatus.UNKNOWN
    active_connections: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class Evidence(_Base):
    """A generic piece of retrieved evidence.

    Used to represent both *similar historical incidents* and *relevant
    runbooks* in the incident context, and is what the ``incident_evidence``
    table persists. Type-specific fields live in ``metadata``.
    """

    source: str
    evidence_type: EvidenceType
    title: str
    content: str = ""
    timestamp: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ServiceTopology(_Base):
    """The dependency context for an affected service, with EXPLICIT semantics.

    Direct (one hop):
      * ``direct_dependencies`` — edges ``service -> X`` (services it directly
        depends on, i.e. direct downstream), with protocol/relationship detail.
      * ``direct_dependents``  — edges ``Y -> service`` (services that directly
        depend on it, i.e. direct upstream).
      * ``direct_downstream`` / ``direct_upstream`` — the same as flat name lists.

    Transitive (recursive) — named so they are never confused with direct edges:
      * ``dependency_chain`` — all services ``service`` transitively depends on.
      * ``dependent_chain``  — all services that transitively depend on ``service``.
    """

    service: str
    direct_dependencies: list[ServiceDependency] = Field(default_factory=list)
    direct_dependents: list[ServiceDependency] = Field(default_factory=list)
    direct_downstream: list[str] = Field(default_factory=list)
    direct_upstream: list[str] = Field(default_factory=list)
    dependency_chain: list[str] = Field(default_factory=list)
    dependent_chain: list[str] = Field(default_factory=list)


class MissingInfo(_Base):
    """A recorded gap or failure encountered while building the context."""

    source: str
    detail: str


class IncidentContext(_Base):
    """The unified Phase 1 deliverable for a single incident."""

    incident: Incident
    affected_service: Service | None = None
    dependency_path: ServiceTopology
    telemetry: list[TelemetrySnapshot] = Field(default_factory=list)
    recent_deployments: list[Deployment] = Field(default_factory=list)
    similar_incidents: list[Evidence] = Field(default_factory=list)
    relevant_runbooks: list[Evidence] = Field(default_factory=list)
    missing_information: list[MissingInfo] = Field(default_factory=list)
