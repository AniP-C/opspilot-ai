"""Database layer: SQLAlchemy engine, ORM models and repositories.

PostgreSQL is the canonical operational store. ORM columns use only
cross-compatible types (String/Text/Integer/Float/DateTime/JSON) so the exact
same models run on SQLite for zero-setup local dev and the test suite.

Vectors are NEVER stored here — semantic knowledge lives in Chroma.
"""
