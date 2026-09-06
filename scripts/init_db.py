"""Create all database tables. Usage: ``python -m scripts.init_db``."""

from __future__ import annotations

from app.config import get_settings
from app.db.base import create_all, get_engine
from app.logging_config import configure_logging, get_logger


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger("scripts.init_db")
    engine = get_engine()
    create_all(engine)
    logger.info("init_db: created tables on %s", engine.url.render_as_string(hide_password=True))


if __name__ == "__main__":
    main()
