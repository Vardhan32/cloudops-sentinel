"""Shared pytest fixtures: isolated temp SQLite DB, monitor loop disabled."""

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

_tmp = tempfile.mkdtemp(prefix="sentinel-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["SENTINEL_ENABLE_MONITOR"] = "0"

from app.database import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ServiceTarget  # noqa: E402


@pytest.fixture()
def db():
    init_db()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db):
    # Clean all tables between tests for determinism.
    from app.models import Incident, MetricSnapshot, ServiceCheckResult

    for model in (ServiceCheckResult, ServiceTarget, Incident, MetricSnapshot):
        db.query(model).delete()
    db.commit()
    with TestClient(app) as c:
        yield c
