"""HTTP routes for the Ops Context Engine."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.dependencies import get_chroma_store, make_context_builder
from app.config import Settings, get_settings
from app.db.base import get_session
from app.db.repositories import ServiceRepository
from app.domain.models import IncidentContext, Service
from app.investigation.engine import make_investigation_engine
from app.investigation.models import InvestigationResult
from app.investigation.store import InvestigationStore
from app.knowledge.chroma_store import HISTORICAL_COLLECTION, RUNBOOK_COLLECTION
from app.logging_config import get_logger
from app.remediation.models import AuditEvent, RemediationView, VerificationResult
from app.remediation.service import make_remediation_service

logger = get_logger(__name__)

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    database: str
    chroma: str
    servicenow_mode: str
    newrelic_mode: str
    historical_documents: int | None = None
    runbook_documents: int | None = None


@router.get("/health", response_model=HealthResponse, tags=["ops"])
def health(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> HealthResponse:
    # Database check
    try:
        session.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:  # noqa: BLE001
        logger.error("health: database check failed: %s", exc)
        db_status = f"error: {exc}"

    # Chroma check
    hist_count = run_count = None
    try:
        store = get_chroma_store(settings)
        hist_count = store.count(HISTORICAL_COLLECTION)
        run_count = store.count(RUNBOOK_COLLECTION)
        chroma_status = "ok"
    except Exception as exc:  # noqa: BLE001
        logger.error("health: chroma check failed: %s", exc)
        chroma_status = f"error: {exc}"

    overall = "ok" if db_status == "ok" and chroma_status == "ok" else "degraded"
    return HealthResponse(
        status=overall,
        database=db_status,
        chroma=chroma_status,
        servicenow_mode=settings.servicenow_mode,
        newrelic_mode=settings.newrelic_mode,
        historical_documents=hist_count,
        runbook_documents=run_count,
    )


@router.get("/api/services/{service_id}", response_model=Service, tags=["topology"])
def get_service(
    service_id: str,
    session: Session = Depends(get_session),
) -> Service:
    service = ServiceRepository(session).get_service(service_id)
    if service is None:
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' not found")
    return service


@router.get(
    "/api/incidents/{incident_id}/context",
    response_model=IncidentContext,
    tags=["context"],
)
def get_incident_context(
    incident_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> IncidentContext:
    """Build and return the unified operational context for an incident.

    Phase 1 only: no diagnosis, remediation or approval data is included, and the
    hidden ``ground_truth`` validation payload is never exposed.
    """
    builder = make_context_builder(session, settings)
    # IncidentNotFound / IncidentSourceUnavailable are translated by the
    # application-level exception handlers (404 / 502).
    return builder.build(incident_id)


# --------------------------------------------------------------------------- #
# Phase 2 — Investigation
# --------------------------------------------------------------------------- #
class AdditionalInfoRequest(BaseModel):
    information: str


@router.post(
    "/api/incidents/{incident_id}/investigate",
    response_model=InvestigationResult,
    tags=["investigation"],
)
def investigate_incident(
    incident_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> InvestigationResult:
    """Run the Phase 2 investigation for an incident.

    Builds/reuses the Phase 1 ``IncidentContext``, runs the LangGraph
    investigation, persists it and returns a structured ``InvestigationResult``.
    Advisory only — no remediation is executed and no production system is
    mutated (``execution_available`` is always false).
    """
    engine = make_investigation_engine(session, settings)
    result = engine.investigate(incident_id)  # 404 / 502 via exception handlers
    InvestigationStore(session).save(result, human_facts=[])
    return result


@router.get(
    "/api/investigations/{investigation_id}",
    response_model=InvestigationResult,
    tags=["investigation"],
)
def get_investigation(
    investigation_id: str,
    session: Session = Depends(get_session),
) -> InvestigationResult:
    result = InvestigationStore(session).get_result(investigation_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Investigation '{investigation_id}' not found")
    return result


@router.post(
    "/api/investigations/{investigation_id}/additional-info",
    response_model=InvestigationResult,
    tags=["investigation"],
)
def submit_additional_info(
    investigation_id: str,
    body: AdditionalInfoRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> InvestigationResult:
    """Human-in-the-loop: supply additional information and re-run the
    investigation with it merged in as operator-provided evidence."""
    store = InvestigationStore(session)
    row = store.get(investigation_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Investigation '{investigation_id}' not found")
    human_facts = list(row.human_facts or []) + [body.information]
    engine = make_investigation_engine(session, settings)
    result = engine.investigate(
        row.incident_id, investigation_id=investigation_id, human_facts=human_facts
    )
    store.save(result, human_facts=human_facts)
    return result


# --------------------------------------------------------------------------- #
# Phase 3 — Remediation OS
# --------------------------------------------------------------------------- #
class RemediationPlanRequest(BaseModel):
    investigation_id: str | None = None


class ApprovalRequest(BaseModel):
    approver: str = "operator"
    note: str = ""


class InvestigateRequest(BaseModel):
    information: str


@router.post(
    "/api/incidents/{incident_id}/remediation/plan",
    response_model=RemediationView,
    tags=["remediation"],
)
def plan_remediation(
    incident_id: str,
    body: RemediationPlanRequest | None = None,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RemediationView:
    """Run (or reuse) the Phase 2 investigation, propose an allow-listed action,
    and evaluate it with the deterministic policy engine. If the decision is
    AUTO_EXECUTE, the safe mock action is executed and verified immediately.

    No real infrastructure is ever mutated in mock mode (the default)."""
    svc = make_remediation_service(session, settings)
    return svc.plan(
        incident_id,
        investigation_id=(body.investigation_id if body else None),
    )


@router.get(
    "/api/incidents/{incident_id}/remediation",
    response_model=RemediationView,
    tags=["remediation"],
)
def get_remediation(
    incident_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RemediationView:
    return make_remediation_service(session, settings).get_view(incident_id)


@router.get(
    "/api/incidents/{incident_id}/audit",
    response_model=list[AuditEvent],
    tags=["remediation"],
)
def get_incident_audit(
    incident_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[AuditEvent]:
    return make_remediation_service(session, settings).store.list_audit(incident_id)


@router.get(
    "/api/actions/{action_ref}",
    response_model=RemediationView,
    tags=["remediation"],
)
def get_action(
    action_ref: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RemediationView:
    view = make_remediation_service(session, settings).get_record_view(action_ref)
    if view.record is None:
        raise HTTPException(status_code=404, detail=f"Action '{action_ref}' not found")
    return view


@router.get(
    "/api/actions/{action_ref}/verification",
    response_model=VerificationResult,
    tags=["remediation"],
)
def get_action_verification(
    action_ref: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> VerificationResult:
    view = make_remediation_service(session, settings).get_record_view(action_ref)
    if view.record is None or view.record.verification is None:
        raise HTTPException(status_code=404, detail="No verification result for this action")
    return view.record.verification


@router.post(
    "/api/actions/{action_ref}/approve",
    response_model=RemediationView,
    tags=["remediation"],
)
def approve_action(
    action_ref: str,
    body: ApprovalRequest | None = None,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RemediationView:
    svc = make_remediation_service(session, settings)
    return svc.approve(
        action_ref,
        approver=(body.approver if body else "operator"),
        note=(body.note if body else ""),
    )


@router.post(
    "/api/actions/{action_ref}/reject",
    response_model=RemediationView,
    tags=["remediation"],
)
def reject_action(
    action_ref: str,
    body: ApprovalRequest | None = None,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RemediationView:
    svc = make_remediation_service(session, settings)
    return svc.reject(
        action_ref,
        approver=(body.approver if body else "operator"),
        note=(body.note if body else ""),
    )


@router.post(
    "/api/actions/{action_ref}/investigate",
    response_model=RemediationView,
    tags=["remediation"],
)
def investigate_action_further(
    action_ref: str,
    body: InvestigateRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RemediationView:
    svc = make_remediation_service(session, settings)
    return svc.investigate_further(action_ref, body.information)


@router.post(
    "/api/actions/{action_ref}/execute",
    response_model=RemediationView,
    tags=["remediation"],
)
def execute_action(
    action_ref: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RemediationView:
    """Execute an action that policy authorised (AUTO_APPROVED) or a human approved.
    Execution is always mock/dry-run by default and is idempotent."""
    svc = make_remediation_service(session, settings)
    return svc.execute(action_ref)


@router.post(
    "/api/actions/{action_ref}/rollback",
    response_model=RemediationView,
    tags=["remediation"],
)
def rollback_action(
    action_ref: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RemediationView:
    svc = make_remediation_service(session, settings)
    return svc.rollback(action_ref)
