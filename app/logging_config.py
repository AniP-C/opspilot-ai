"""Central logging configuration.

We log important workflow events (incident retrieval, topology lookup, telemetry
lookup, deployment retrieval, retrieval operations, context-build completion and
integration failures). We never log credentials or secrets — only identifiers,
counts and status.
"""

from __future__ import annotations

import logging

_CONFIGURED = False


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging once (idempotent)."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
    )
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a module logger (configures logging lazily on first use)."""
    if not _CONFIGURED:
        configure_logging()
    return logging.getLogger(name)
