"""Seed the operational store from the synthetic dataset.

Usage: ``python -m scripts.seed_db``. Creates tables first if needed.
"""

from __future__ import annotations

from app.config import get_settings
from app.db.base import create_all, get_engine, get_session_factory
from app.db.seed import seed_database
from app.logging_config import configure_logging, get_logger


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger("scripts.seed_db")

    create_all(get_engine())
    factory = get_session_factory()
    with factory() as session:
        counts = seed_database(session, settings.data_dir)
    logger.info("seed_db: done -> %s", counts)


if __name__ == "__main__":
    main()
