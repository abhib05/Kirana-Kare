"""Shared fixtures.

DB tests run against a real PostgreSQL (`TEST_DATABASE_URL`, default: the compose
`kirana_test` database on localhost:5433) because the append-only trigger and views are
Postgres features. They are skipped automatically when the database is unreachable.
Redis is replaced by fakeredis everywhere.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.seed import seed

BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://kk:kk@localhost:5433/kirana_test"
)
SHOP_ID = 1

_TABLES = (
    "audit_log, reminders, restock_drafts, ledger_entries, matches, upi_alerts, basket_items, "
    "baskets, products, vendors, customer_aliases, customers, shops"
)


def _migrate(url: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError:
        pytest.skip("PostgreSQL test database not reachable (run `docker compose up -d db`)")
    _migrate(TEST_DATABASE_URL)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        seed(db, SHOP_ID)
    return factory


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as session:
        yield session


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    from app.db.session import get_db
    from app.main import app

    def _get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def redis_client() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def settings():
    return get_settings()
