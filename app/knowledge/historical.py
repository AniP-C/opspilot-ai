"""Semantic retrieval of similar historical incidents from Chroma.

This layer only *retrieves* context — it never diagnoses a root cause. It accepts
a query (typically the current incident's title + description), optional metadata
filters, and returns ranked ``Evidence`` items.
"""

from __future__ import annotations

from app.domain.enums import EvidenceType
from app.domain.errors import RetrievalError
from app.domain.models import Evidence
from app.knowledge.chroma_store import HISTORICAL_COLLECTION, ChromaStore
from app.logging_config import get_logger
from app.timeutils import parse_ts

logger = get_logger(__name__)


class ChromaHistoricalRetriever:
    def __init__(self, store: ChromaStore, default_top_k: int = 5) -> None:
        self._store = store
        self._default_top_k = default_top_k

    def search(
        self,
        query: str,
        *,
        where: dict | None = None,
        top_k: int | None = None,
    ) -> list[Evidence]:
        k = top_k or self._default_top_k
        try:
            rows = self._store.query(
                HISTORICAL_COLLECTION, query_text=query, n_results=k, where=where
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("retrieval: historical incident search failed: %s", exc)
            raise RetrievalError(f"historical retrieval failed: {exc}") from exc

        logger.info(
            "retrieval: historical incidents -> %d result(s) (filter=%s)",
            len(rows),
            where or {},
        )
        evidence: list[Evidence] = []
        for row in rows:
            meta = dict(row.get("metadata") or {})
            distance = row.get("distance")
            score = None if distance is None else round(1.0 - float(distance), 4)
            meta_out = {
                **meta,
                "incident_id": row.get("id"),
                "distance": distance,
                "score": score,
            }
            evidence.append(
                Evidence(
                    source=f"chroma:{HISTORICAL_COLLECTION}",
                    evidence_type=EvidenceType.HISTORICAL_INCIDENT,
                    title=str(meta.get("title") or row.get("id") or "historical incident"),
                    content=row.get("document") or "",
                    timestamp=parse_ts(meta.get("created_at")),
                    metadata=meta_out,
                )
            )
        return evidence
