"""Database engine, session factory and portable column types."""
from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]
from sqlalchemy import Text, create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from app.config import settings
from app.logging_config import get_logger

log = get_logger("regulens.db")


class Base(DeclarativeBase):
    pass


class PortableVector(TypeDecorator):
    """A vector column that works with and without pgvector.

    * PostgreSQL + pgvector available -> native ``VECTOR(n)`` with ANN indexes.
    * PostgreSQL without pgvector       -> ``TEXT`` (JSON encoded).
    * SQLite                            -> ``TEXT`` (JSON encoded).

    Search code asks :class:`app.retrieval.vector.VectorStore` whether the
    native operator is available; if not, cosine similarity is computed in
    Python with numpy. Embeddings are therefore a soft dependency.
    """

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql" and _pgvector_available:
            return dialect.type_descriptor(Vector(settings.EMBEDDING_DIMENSION))
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if dialect.name == "postgresql" and _pgvector_available:
            return list(value)
        if isinstance(value, (list, tuple)):
            return json.dumps([float(x) for x in value])
        return value

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (ValueError, TypeError):
                return None
        if isinstance(value, (list, tuple)):
            return [float(x) for x in value]
        return value


_pgvector_available = False
_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _make_engine() -> Engine:
    global _pgvector_available
    kwargs: dict[str, Any] = {}
    if settings.is_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False}
        engine = create_engine(settings.DATABASE_URL, echo=settings.DB_ECHO, future=True, **kwargs)

        @event.listens_for(engine, "connect")
        def _sqlite_pragma(dbapi_conn: Any, _rec: Any) -> None:  # pragma: no cover
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        _pgvector_available = False
        return engine

    kwargs = {
        "pool_size": settings.DB_POOL_SIZE,
        "max_overflow": settings.DB_MAX_OVERFLOW,
        "pool_pre_ping": True,
        "echo": settings.DB_ECHO,
        "future": True,
        "connect_args": {"connect_timeout": settings.DB_CONNECT_TIMEOUT},
    }
    engine = create_engine(settings.DATABASE_URL, **kwargs)
    _pgvector_available = _detect_pgvector(engine)
    log.info("database.connected", url=_redact(settings.DATABASE_URL), pgvector=_pgvector_available)
    return engine


def _detect_pgvector(engine: Engine) -> bool:
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
            ).first()
            if not row:
                return False
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.commit()
        return True
    except Exception as exc:  # pragma: no cover - environment dependent
        log.warning("database.pgvector_unavailable", error=str(exc))
        return False


def _redact(url: str) -> str:
    if "@" not in url:
        return url
    head, _, tail = url.partition("@")
    if ":" in head:
        scheme, _, _ = head.partition("://")
        creds = head.split("://", 1)[-1]
        user = creds.split(":", 1)[0]
        return f"{scheme}://{user}:***@{tail}"
    return f"***@{tail}"


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = _make_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    return _session_factory


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context manager for scripts / background work."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def pgvector_enabled() -> bool:
    return _pgvector_available


def reset_engine_for_tests() -> None:
    global _engine, _session_factory, _pgvector_available
    _engine = None
    _session_factory = None
    _pgvector_available = False
