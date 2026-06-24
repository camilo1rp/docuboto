"""Test del endpoint /health usando una conexión mockeada (sin BD real, ADR-003)."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db_conn
from app.main import app


def _mock_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval.return_value = 1
    return conn


@pytest.fixture
def client() -> TestClient:
    async def _override():
        yield _mock_conn()

    app.dependency_overrides[get_db_conn] = _override
    # Sin 'with': no se dispara el lifespan (que abriría un pool real a Postgres);
    # la conexión va mockeada por la dependencia.
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health_ok(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
