"""Ingest historical incidents and runbooks into Chroma with filterable metadata.

Documents (the embedded text) are composed from the observable, symptom-facing
fields so a current incident's title/description retrieves the right history and
runbooks. Metadata carries the structured fields used for filtering and display.
"""

from __future__ import annotations

from pathlib import Path

from app import dataset
from app.knowledge.chroma_store import (
    HISTORICAL_COLLECTION,
    RUNBOOK_COLLECTION,
    ChromaStore,
)
from app.logging_config import get_logger

logger = get_logger(__name__)


def _historical_document(inc: dict) -> str:
    symptoms = "; ".join(inc.get("symptoms", []) or [])
    return (
        f"{inc.get('title', '')}\n"
        f"{inc.get('description', '')}\n"
        f"Symptoms: {symptoms}\n"
        f"Component: {inc.get('affected_component', '')}\n"
        f"Fingerprint: {inc.get('incident_fingerprint', '')}\n"
        f"Root cause: {inc.get('root_cause', '')}\n"
        f"Resolution: {inc.get('resolution', '')}"
    )


def _historical_metadata(inc: dict) -> dict:
    # Chroma metadata values must be scalars (str/int/float/bool).
    meta = {
        "title": inc.get("title", ""),
        "service": inc.get("service", ""),
        "severity": inc.get("severity", ""),
        "environment": inc.get("environment", "production"),
        "document_type": "historical_incident",
        "root_cause": inc.get("root_cause", ""),
        "resolution": inc.get("resolution", ""),
        "resolution_success": bool(inc.get("resolution_success", False)),
        "affected_component": inc.get("affected_component", ""),
        "incident_fingerprint": inc.get("incident_fingerprint", ""),
        "duration_minutes": int(inc.get("duration_minutes", 0) or 0),
        "created_at": inc.get("created_at", ""),
        "resolved_at": inc.get("resolved_at", ""),
    }
    # NOTE: evaluation-only / ground-truth-derived fields (e.g.
    # ``ground_truth.related_deployment_id``) are DELIBERATELY NOT ingested into the
    # runtime Chroma knowledge store. Only observable, symptom-facing historical
    # fields belong here; the hidden ``ground_truth`` payload is for the evaluation
    # framework alone and must never surface through runtime retrieval / ``/context``.
    return meta


def _runbook_metadata(rb: dict) -> dict:
    services = rb.get("applicable_services", []) or []
    return {
        "title": rb.get("title", ""),
        "document_type": "runbook",
        # Lists aren't valid Chroma metadata -> store a delimited string plus a
        # primary service for optional filtering.
        "applicable_services": ",".join(services),
        "service": services[0] if services else "",
        "source_path": rb.get("source_path", ""),
    }


def ingest_knowledge(store: ChromaStore, data_dir: str | Path) -> dict[str, int]:
    """(Re)build the historical-incident and runbook collections. Idempotent."""
    data_dir = Path(data_dir)

    # Historical incidents ------------------------------------------------
    store.recreate_collection(HISTORICAL_COLLECTION)
    historical = dataset.load_historical_incidents(data_dir)
    if historical:
        store.add(
            HISTORICAL_COLLECTION,
            ids=[inc["incident_id"] for inc in historical],
            documents=[_historical_document(inc) for inc in historical],
            metadatas=[_historical_metadata(inc) for inc in historical],
        )

    # Runbooks ------------------------------------------------------------
    store.recreate_collection(RUNBOOK_COLLECTION)
    runbooks = dataset.iter_runbooks(data_dir)
    if runbooks:
        store.add(
            RUNBOOK_COLLECTION,
            ids=[rb["id"] for rb in runbooks],
            documents=[rb["content"] for rb in runbooks],
            metadatas=[_runbook_metadata(rb) for rb in runbooks],
        )

    counts = {"historical_incidents": len(historical), "runbooks": len(runbooks)}
    logger.info("ingest_knowledge: %s", counts)
    return counts
