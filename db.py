"""Database helpers for SQLAlchemy engine and session.

Builds engine from DATABASE_URL env or fallback sqlite:///resume.db. Provides
SessionLocal (scoped_session) and init_db().
"""

from __future__ import annotations

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker


def _make_engine_url() -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    return "sqlite:///resume.db"


# echo can be toggled with SQL_ECHO=1
engine = create_engine(
    _make_engine_url(), echo=os.getenv("SQL_ECHO") == "1", future=True
)

# Thread-local scoped session
SessionLocal = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False))


def init_db(Base) -> None:
    """Create all tables for the provided declarative Base."""
    Base.metadata.create_all(engine)
