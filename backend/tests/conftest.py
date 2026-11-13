import os
from pathlib import Path

import pytest

# Tests run against a real PostgreSQL. Default matches docker-compose.test.yml.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://consultflow:consultflow@localhost:55441/consultflow_test"
)
os.environ.setdefault("STATIC_DIR", "/nonexistent")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402

SAMPLES = Path(__file__).resolve().parents[2] / "samples"


@pytest.fixture(scope="session", autouse=True)
def _schema():
    assert engine.dialect.name == "postgresql", "tests must run against PostgreSQL"
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture(autouse=True)
def _clean():
    yield
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def samples() -> Path:
    return SAMPLES
