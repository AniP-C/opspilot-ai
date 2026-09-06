"""FastAPI application factory for OpsPilot AI — Phase 1."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.config import get_settings
from app.domain.errors import (
    IncidentNotFound,
    IncidentSourceUnavailable,
    OpsPilotError,
)
from app.logging_config import configure_logging, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info(
        "startup: OpsPilot Phase 1 | servicenow=%s newrelic=%s embeddings=%s",
        settings.servicenow_mode,
        settings.newrelic_mode,
        settings.embedding_backend,
    )
    yield
    logger.info("shutdown: OpsPilot Phase 1")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="OpsPilot AI — AI Operations OS",
        version="0.3.0",
        description=(
            "An AI operations control plane. Phase 1 gathers unified operational "
            "context for an incident; Phase 2 investigates it (LangGraph) and "
            "produces an evidence-grounded diagnosis; Phase 3 (Remediation OS) "
            "proposes an allow-listed action, evaluates it with a deterministic "
            "risk/policy engine (AUTO_EXECUTE / ASK_HUMAN / ESCALATE / REJECT), "
            "executes it through a controlled mock executor, verifies real recovery "
            "from telemetry, rolls back on failure, writes back to ServiceNow and "
            "records a full audit trail. The LLM never controls infrastructure and "
            "no real system is mutated in the default mock mode."
        ),
        lifespan=lifespan,
    )

    # --- domain error -> HTTP status mapping ------------------------------
    @app.exception_handler(IncidentNotFound)
    async def _incident_not_found(_: Request, exc: IncidentNotFound):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(IncidentSourceUnavailable)
    async def _incident_source_unavailable(_: Request, exc: IncidentSourceUnavailable):
        # 502: we could not reach the incident system of record. We never
        # fabricate an incident.
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(OpsPilotError)
    async def _opspilot_error(_: Request, exc: OpsPilotError):
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    # Phase 3: a remediation lifecycle precondition was violated (e.g. approving
    # an action that is not awaiting approval) -> 409 Conflict.
    from app.remediation.service import RemediationError

    @app.exception_handler(RemediationError)
    async def _remediation_error(_: Request, exc: RemediationError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    app.include_router(router)
    return app


app = create_app()
