"""Engine / session factory and the declarative base.

The application's default engine is built lazily from ``Settings.database_url``.
Tests build their own engine against a temporary SQLite database and pass
sessions to repositories directly, so they never touch the global engine.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def make_engine(database_url: str) -> Engine:
    """Create an engine, applying SQLite-specific connect args when needed."""
    connect_args: dict = {}
    if database_url.startswith("sqlite"):
        # Allow use across threads (FastAPI / Streamlit) for file & memory DBs.
        connect_args["check_same_thread"] = False
    return create_engine(database_url, future=True, connect_args=connect_args)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return make_engine(get_settings().database_url)


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return make_session_factory(get_engine())


def create_all(engine: Engine | None = None) -> None:
    """Create all tables. Imports models so they register on ``Base.metadata``."""
    from app.db import models  # noqa: F401  (side-effect: register tables)

    Base.metadata.create_all(bind=engine or get_engine())


def get_session() -> Iterator[Session]:
    """FastAPI dependency: yield a session and always close it."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()
