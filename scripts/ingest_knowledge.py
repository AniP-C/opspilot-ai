"""Ingest historical incidents and runbooks into Chroma.

Usage: ``python -m scripts.ingest_knowledge``.
"""

from __future__ import annotations

from app.api.dependencies import get_chroma_store
from app.config import get_settings
from app.knowledge.ingest import ingest_knowledge
from app.logging_config import configure_logging, get_logger


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger("scripts.ingest_knowledge")

    store = get_chroma_store(settings)
    counts = ingest_knowledge(store, settings.data_dir)
    logger.info("ingest_knowledge: done -> %s", counts)


if __name__ == "__main__":
    main()
