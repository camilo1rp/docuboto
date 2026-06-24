# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de consulta/verificación de auditoría (E08, ADR-008)."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from orpycamcp_common import audit as audit_lib

from app.core.authz import Caller, get_caller
from app.core.database import get_tenant_conn
from app.main import app

TENANT_HEADERS = {"X-Tenant-Slug": "demo"}
_ADMIN = Caller(
    user_id=uuid.uuid4(),
    keycloak_sub="kc-admin",
    is_root=True,
    permissions={"USUA_PERM_ADMIN": 5},
)


def _override(conn: AsyncMock, caller: Caller = _ADMIN) -> None:
    async def _dep():
        yield conn

    app.dependency_overrides[get_tenant_conn] = _dep
    app.dependency_overrides[get_caller] = lambda: caller


def _audit_row(*, ts=None, **kw) -> dict:
    base = {
        "id": 1,
        "ts": ts or datetime.now(tz=UTC),
        "service": "auth-service",
        "actor": "jperez",
        "canal": "api",
        "action": "auth.login_success",
        "object_type": "auth_session",
        "object_ref": "jperez",
        "payload": {},
        "prev_hash": None,
        "hash": "x",
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_list_audit(client: AsyncClient) -> None:
    conn = AsyncMock()
    conn.fetchval.return_value = 1  # count
    conn.fetch.return_value = [_audit_row()]
    _override(conn)

    resp = await client.get("/api/v1/audit?action=auth.login_success", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.headers["x-total-count"] == "1"
    body = resp.json()
    assert len(body) == 1
    assert body[0]["action"] == "auth.login_success"


@pytest.mark.asyncio
async def test_verify_ok(client: AsyncClient) -> None:
    # Cadena válida de una fila: hash = compute_hash(None, ...campos...).
    good_hash = audit_lib.compute_hash(
        None,
        tenant_slug="demo",
        service="auth-service",
        actor="jperez",
        canal="api",
        action="auth.login_success",
        object_type="auth_session",
        object_ref="jperez",
        payload={},
    )
    conn = AsyncMock()
    conn.fetch.return_value = [_audit_row(hash=good_hash, prev_hash=None)]
    _override(conn)

    resp = await client.get("/api/v1/audit/verify", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["broken_id"] is None
    assert body["tenant_slug"] == "demo"


@pytest.mark.asyncio
async def test_verify_broken(client: AsyncClient) -> None:
    conn = AsyncMock()
    conn.fetch.return_value = [_audit_row(id=7, hash="tampered", prev_hash=None)]
    _override(conn)

    resp = await client.get("/api/v1/audit/verify", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["broken_id"] == 7


@pytest.mark.asyncio
async def test_audit_gate_denies_without_admin(client: AsyncClient) -> None:
    conn = AsyncMock()
    _override(
        conn,
        Caller(
            user_id=uuid.uuid4(),
            keycloak_sub="kc-op",
            is_root=False,
            permissions={"PERM_RADI": 3},
        ),
    )
    resp = await client.get("/api/v1/audit", headers=TENANT_HEADERS)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_audit_missing_tenant_is_400(client: AsyncClient) -> None:
    conn = AsyncMock()
    _override(conn)
    resp = await client.get("/api/v1/audit")  # sin X-Tenant-Slug
    assert resp.status_code == 400
