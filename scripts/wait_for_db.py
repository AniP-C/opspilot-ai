"""Block until the configured database accepts connections (used by Docker entrypoint)."""

from __future__ import annotations

import sys
import time

from sqlalchemy import text

from app.config import get_settings
from app.db.base import make_engine
from app.logging_config import configure_logging, get_logger


def main(max_attempts: int = 30, delay: float = 2.0) -> None:
    configure_logging(get_settings().log_level)
    logger = get_logger("scripts.wait_for_db")
    url = get_settings().database_url
    engine = make_engine(url)
    for attempt in range(1, max_attempts + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("wait_for_db: database ready after %d attempt(s)", attempt)
            return
        except Exception as exc:  # noqa: BLE001
            logger.info("wait_for_db: attempt %d/%d not ready (%s)", attempt, max_attempts, exc)
            time.sleep(delay)
    logger.error("wait_for_db: database not ready after %d attempts", max_attempts)
    sys.exit(1)


if __name__ == "__main__":
    main()
