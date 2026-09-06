"""Dependency wiring.

Builds a fully-assembled ``ContextBuilder`` from configuration, selecting mock or
real integration clients per ``*_MODE`` env var. The Chroma store is cached
across requests (constructing it is comparatively expensive).
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.context.builder import ContextBuilder
from app.db.repositories import (
    DeploymentRepository,
    EvidenceRepository,
    IncidentRepository,
    ServiceRepository,
    TelemetryRepository,
    TopologyRepository,
)
from app.domain.errors import TelemetryUnavailable
from app.domain.models import TelemetrySnapshot
from app.integrations.newrelic.client import NewRelicClient
from app.integrations.newrelic.mock_client import MockNewRelicClient
from app.integrations.servicenow.client import ServiceNowClient
from app.integrations.servicenow.mock_client import MockServiceNowClient
from app.knowledge.chroma_store import ChromaStore
from app.knowledge.historical import ChromaHistoricalRetriever
from app.knowledge.runbooks import ChromaRunbookRetriever
from app.logging_config import get_logger
from app.topology.service import TopologyService

logger = get_logger(__name__)


class _UnavailableTelemetrySource:
    """Stand-in telemetry source used when a real client can't be constructed
    (e.g. missing credentials). Makes the builder degrade gracefully rather than
    crash: every call raises, which the builder records as missing information."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def get_service_health(self, service: str, at: datetime | None = None) -> TelemetrySnapshot | None:
        raise TelemetryUnavailable(self._reason)

    def get_error_rate(self, service: str, at: datetime | None = None):
        raise TelemetryUnavailable(self._reason)

    def get_latency(self, service: str, at: datetime | None = None):
        raise TelemetryUnavailable(self._reason)

    def get_recent_errors(self, service: str, at: datetime | None = None) -> list[str]:
        return []


@lru_cache(maxsize=4)
def _chroma_store_cached(path: str, backend: str, dim: int) -> ChromaStore:
    return ChromaStore(path, embedding_backend=backend, embedding_dim=dim)


def get_chroma_store(settings: Settings | None = None) -> ChromaStore:
    settings = settings or get_settings()
    return _chroma_store_cached(
        settings.chroma_path, settings.embedding_backend, settings.embedding_dim
    )


def _make_incident_source(session: Session, settings: Settings):
    if settings.servicenow_is_mock:
        return MockServiceNowClient(IncidentRepository(session))
    # Real mode: construction raises IncidentSourceUnavailable if unconfigured,
    # which the API maps to 502 (the incident is a mandatory input).
    return ServiceNowClient(
        settings.servicenow_url, settings.servicenow_user, settings.servicenow_token
    )


def _make_telemetry_source(session: Session, settings: Settings):
    if settings.newrelic_is_mock:
        return MockNewRelicClient(TelemetryRepository(session))
    try:
        return NewRelicClient(settings.newrelic_api_key, settings.newrelic_account_id)
    except TelemetryUnavailable as exc:
        # Telemetry is optional context -> degrade gracefully.
        logger.warning("telemetry: real client unavailable (%s); degrading", exc)
        return _UnavailableTelemetrySource(str(exc))


def make_context_builder(session: Session, settings: Settings | None = None) -> ContextBuilder:
    settings = settings or get_settings()
    store = get_chroma_store(settings)
    return ContextBuilder(
        incident_source=_make_incident_source(session, settings),
        service_repo=ServiceRepository(session),
        topology_service=TopologyService(TopologyRepository(session)),
        telemetry_source=_make_telemetry_source(session, settings),
        deployment_repo=DeploymentRepository(session),
        historical_retriever=ChromaHistoricalRetriever(store, settings.historical_top_k),
        runbook_retriever=ChromaRunbookRetriever(store, settings.runbook_top_k),
        deployment_window_minutes=settings.deployment_window_minutes,
        deployment_scope=settings.deployment_scope,
        historical_top_k=settings.historical_top_k,
        runbook_top_k=settings.runbook_top_k,
        evidence_repo=EvidenceRepository(session),
    )
