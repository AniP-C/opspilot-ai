"""Embedding functions for Chroma.

The default ``HashingEmbeddingFunction`` maps text into a fixed-dimension vector
using a stable content hash (md5) over unigrams and bigrams, then L2-normalises.
Properties that matter here:

* **Deterministic** across processes/runs (md5, not Python's salted ``hash``),
  which keeps orchestration reproducible.
* **Offline** — no model download, no network, no credentials.
* **Lexical** — captures token/phrase overlap, which is exactly what matches an
  incident's symptom text against historical incidents and runbooks.

An optional ``sentence-transformers`` backend can be selected via configuration
for higher-quality semantic similarity.
"""

from __future__ import annotations

import hashlib
import math
import re

from chromadb.api.types import Documents, EmbeddingFunction, Embeddings

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if len(t) >= 2]


def _hash_index(token: str, dim: int) -> int:
    digest = hashlib.md5(token.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "little") % dim


class HashingEmbeddingFunction(EmbeddingFunction):
    """Deterministic, offline hashing embedder (Chroma ``EmbeddingFunction``)."""

    def __init__(self, dim: int = 1024) -> None:
        self._dim = int(dim)

    def __call__(self, input: Documents) -> Embeddings:  # noqa: A002 (Chroma's arg name)
        return [self._embed(text) for text in input]

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        tokens = _tokenize(text or "")
        # Unigrams.
        for tok in tokens:
            vec[_hash_index(tok, self._dim)] += 1.0
        # Bigrams (weighted lower) capture short phrases like "connection pool".
        for a, b in zip(tokens, tokens[1:]):
            vec[_hash_index(f"{a}_{b}", self._dim)] += 0.5
        # L2 normalise; guarantee a non-zero vector so cosine distance is defined.
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            vec[0] = 1.0
            return vec
        return [v / norm for v in vec]

    # -- forward-compatible metadata for Chroma serialization --------------
    @staticmethod
    def name() -> str:
        return "opspilot-hashing"

    def get_config(self) -> dict:
        return {"dim": self._dim}

    @staticmethod
    def build_from_config(config: dict) -> "HashingEmbeddingFunction":
        return HashingEmbeddingFunction(dim=int(config.get("dim", 1024)))


def get_embedding_function(backend: str = "hash", dim: int = 1024) -> EmbeddingFunction:
    """Return the configured embedding function.

    ``hash`` (default) -> offline ``HashingEmbeddingFunction``.
    ``sentence_transformers`` -> MiniLM via chromadb's helper (requires the
    optional dependency; downloads a model on first use).
    """
    backend = (backend or "hash").strip().lower()
    if backend in {"hash", "hashing", "local"}:
        return HashingEmbeddingFunction(dim=dim)
    if backend in {"sentence_transformers", "st", "sbert"}:
        try:
            from chromadb.utils import embedding_functions
        except Exception as exc:  # pragma: no cover - optional path
            raise RuntimeError(
                "sentence_transformers backend requested but unavailable; "
                "`pip install sentence-transformers`"
            ) from exc
        return embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        )
    raise ValueError(f"Unknown EMBEDDING_BACKEND: {backend!r}")
