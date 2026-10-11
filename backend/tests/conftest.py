"""Pytest configuration: isolated SQLite database per test."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Isolated DB *before* any app import so Settings picks it up.
_TEST_DIR = Path(os.environ.get("REGULENS_TEST_DIR", str(Path.cwd() / ".test_tmp")))
_TEST_DIR.mkdir(parents=True, exist_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{(_TEST_DIR / 'regulens_test.db').as_posix()}"
os.environ["DETERMINISTIC_MODE"] = "true"
os.environ["GEMINI_ENABLED"] = "false"
os.environ["CEREBRAS_ENABLED"] = "false"
os.environ["GEMINI_API_KEY"] = ""
os.environ["CEREBRAS_API_KEY"] = ""
os.environ["AUTH_ENABLED"] = "false"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db import Base


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "t.db"


@pytest.fixture()
def session_factory(db_path):
    url = f"sqlite:///{db_path.as_posix()}"
    engine = create_engine(url, connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(bind=engine)

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture()
def db(session_factory):
    s = session_factory()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


@pytest.fixture()
def client(session_factory):
    from app.db import get_db
    from app.main import app

    def _override():
        s = session_factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
