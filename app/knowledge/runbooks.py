"""Semantic retrieval of relevant runbooks from Chroma.

Returns ranked ``Evidence`` items with retrieval metadata preserved (runbook id,
applicable services, document type, score).
"""

from __future__ import annotations

from app.domain.enums import EvidenceType
from app.domain.errors import RetrievalError
from app.domain.models import Evidence
from app.knowledge.chroma_store import RUNBOOK_COLLECTION, ChromaStore
from app.logging_config import get_logger

logger = get_logger(__name__)


class ChromaRunbookRetriever:
    def __init__(self, store: ChromaStore, default_top_k: int = 3) -> None:
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
                RUNBOOK_COLLECTION, query_text=query, n_results=k, where=where
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("retrieval: runbook search failed: %s", exc)
            raise RetrievalError(f"runbook retrieval failed: {exc}") from exc

        logger.info(
            "retrieval: runbooks -> %d result(s) (filter=%s)", len(rows), where or {}
        )
        evidence: list[Evidence] = []
        for row in rows:
            meta = dict(row.get("metadata") or {})
            distance = row.get("distance")
            score = None if distance is None else round(1.0 - float(distance), 4)
            meta_out = {
                **meta,
                "runbook_id": row.get("id"),
                "distance": distance,
                "score": score,
            }
            evidence.append(
                Evidence(
                    source=f"chroma:{RUNBOOK_COLLECTION}",
                    evidence_type=EvidenceType.RUNBOOK,
                    title=str(meta.get("title") or row.get("id") or "runbook"),
                    content=row.get("document") or "",
                    timestamp=None,
                    metadata=meta_out,
                )
            )
        return evidence
