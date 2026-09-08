"""SQLAlchemy engine, session factory, and declarative base.

Nothing here is model-specific; ORM models arrive in M1 under
``invoice_ops.models`` and inherit from :class:`Base`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from invoice_ops.config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine() -> Engine:
    settings = get_settings()
    is_sqlite = settings.database_url.startswith("sqlite")
    connect_args: dict[str, Any] = {"check_same_thread": False} if is_sqlite else {}

    engine = create_engine(
        settings.database_url,
        echo=settings.sql_echo,
        pool_pre_ping=True,
        connect_args=connect_args,
    )

    if is_sqlite:
        # SQLite ignores foreign keys unless told otherwise per-connection.
        @event.listens_for(engine, "connect")
        def _enable_sqlite_fks(dbapi_conn: Any, _record: Any) -> None:
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on exception."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    with session_scope() as session:
        yield session


def check_connection() -> None:
    """Raise if the database is unreachable. Used by the readiness probe."""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
