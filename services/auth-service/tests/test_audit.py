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
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient
from orpycamcp_common import audit as audit_lib

from app.core.authz import Caller, get_caller
from app.core.database import get_tenant_conn
from app.main import app

TENANT_HEADERS = {"X-Tenant-Slug": "demo"}


class _FakeTx:
    """Context manager async no-op para simular conn.transaction()."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> bool:
        return False


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
    conn.transaction = MagicMock(return_value=_FakeTx())  # append_denial() best-effort
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
async def test_admin_sin_clearance_recorta_por_no_read_up(client: AsyncClient) -> None:
    """`USUA_PERM_ADMIN` no es habilitación de seguridad (E08, RF-SEG-08).

    Aquí solo se comprueba el CABLEADO: que el nivel efectivo del llamante llega al
    SQL. Que el SQL de verdad filtre se verifica por mutación contra Postgres real
    en `tests/integration/test_audit_no_read_up.py` — un mock no puede probarlo.
    """
    conn = AsyncMock()
    # fetchval: 1º el clearance del llamante (nivel 2), 2º el COUNT del listado.
    conn.fetchval.side_effect = [2, 0]
    # fetch: 1º las tablas fuente presentes en el schema, 2º las filas.
    conn.fetch.side_effect = [[{"relname": "radicados"}, {"relname": "expedientes"}], []]
    _override(
        conn,
        Caller(
            user_id=uuid.uuid4(),
            keycloak_sub="kc-admin-sin-clearance",
            is_root=False,
            permissions={"USUA_PERM_ADMIN": 5},
        ),
    )

    resp = await client.get("/api/v1/audit", headers=TENANT_HEADERS)
    assert resp.status_code == 200

    sql, *params = conn.fetch.call_args.args
    assert "nivel_seguridad <=" in sql, "el listado tiene que llevar el recorte al SQL"
    assert 2 in params, "el nivel efectivo del llamante debe viajar como parámetro"


@pytest.mark.asyncio
async def test_root_ve_la_auditoria_integra(client: AsyncClient) -> None:
    """Quien alcanza el nivel máximo no sufre recorte: ni una consulta extra ni un
    filtro que mutile el valor probatorio del registro."""
    conn = AsyncMock()
    conn.fetchval.return_value = 1
    conn.fetch.return_value = [_audit_row()]
    _override(conn)  # _ADMIN es is_root=True

    resp = await client.get("/api/v1/audit", headers=TENANT_HEADERS)
    assert resp.status_code == 200

    sql = conn.fetch.call_args.args[0]
    assert "nivel_seguridad" not in sql


@pytest.mark.asyncio
async def test_audit_missing_tenant_is_400(client: AsyncClient) -> None:
    conn = AsyncMock()
    _override(conn)
    resp = await client.get("/api/v1/audit")  # sin X-Tenant-Slug
    assert resp.status_code == 400
