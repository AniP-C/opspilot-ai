"""Thin wrapper around an embedded Chroma ``PersistentClient``.

Two collections are used, each carrying rich metadata so retrieval can be
filtered (service, severity, environment, document_type, incident_fingerprint,
root_cause, ...):

* ``historical_incidents``
* ``runbooks``

Cosine space is configured so normalised embeddings rank by similarity.
"""

from __future__ import annotations

from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.knowledge.embeddings import get_embedding_function
from app.logging_config import get_logger

logger = get_logger(__name__)

HISTORICAL_COLLECTION = "historical_incidents"
RUNBOOK_COLLECTION = "runbooks"


class ChromaStore:
    def __init__(
        self,
        path: str,
        *,
        embedding_backend: str = "hash",
        embedding_dim: int = 1024,
    ) -> None:
        self._client = chromadb.PersistentClient(
            path=path,
            settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True),
        )
        self._embedding_fn = get_embedding_function(embedding_backend, embedding_dim)

    # -- collection access -------------------------------------------------
    def collection(self, name: str):
        return self._client.get_or_create_collection(
            name=name,
            embedding_function=self._embedding_fn,
            metadata={"hnsw:space": "cosine"},
        )

    def recreate_collection(self, name: str):
        """Drop and recreate a collection (used by ingestion for idempotency)."""
        try:
            self._client.delete_collection(name)
        except Exception:  # noqa: BLE001 - fine if it didn't exist
            pass
        return self.collection(name)

    # -- writes ------------------------------------------------------------
    def add(
        self,
        name: str,
        *,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        if not ids:
            return
        self.collection(name).add(ids=ids, documents=documents, metadatas=metadatas)

    def count(self, name: str) -> int:
        return self.collection(name).count()

    # -- reads -------------------------------------------------------------
    def query(
        self,
        name: str,
        *,
        query_text: str,
        n_results: int,
        where: dict | None = None,
    ) -> list[dict[str, Any]]:
        """Return a flat, ranked list of ``{id, document, metadata, distance}``."""
        collection = self.collection(name)
        available = collection.count()
        if available == 0:
            return []
        result = collection.query(
            query_texts=[query_text],
            n_results=max(1, min(n_results, available)),
            where=where or None,
            include=["documents", "metadatas", "distances"],
        )
        ids = (result.get("ids") or [[]])[0]
        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        dists = (result.get("distances") or [[]])[0]
        out: list[dict[str, Any]] = []
        for i, _id in enumerate(ids):
            out.append(
                {
                    "id": _id,
                    "document": docs[i] if i < len(docs) else "",
                    "metadata": metas[i] if i < len(metas) else {},
                    "distance": dists[i] if i < len(dists) else None,
                }
            )
        return out
