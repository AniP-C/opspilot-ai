"""Collaborators the investigation reuses from Phase 1 (behind their interfaces).

This is how Phase 2 *consumes* Phase 1 without duplicating any subsystem: it
holds the same telemetry source, topology service, deployment repository and
retrievers, plus the tuning knobs from settings.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.db.repositories import DeploymentRepository
from app.domain.interfaces import (
    HistoricalIncidentRetriever,
    RunbookRetriever,
    TelemetrySource,
)
from app.topology.service import TopologyService


@dataclass
class InvestigationDeps:
    telemetry_source: TelemetrySource
    topology_service: TopologyService
    deployment_repo: DeploymentRepository
    historical_retriever: HistoricalIncidentRetriever
    runbook_retriever: RunbookRetriever
    deployment_window_minutes: int = 60
    telemetry_relevance_minutes: int = 90
    max_hypotheses: int = 6
    sufficiency_confidence: float = 0.65
    sufficiency_margin: float = 1.0
