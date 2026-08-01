# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de URD y cambio de contexto de dependencia (E08, ADR-013)."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
from httpx import AsyncClient

from app.core.authz import Caller, get_caller
from app.core.database import get_tenant_conn
from app.main import app
from app.services import urd_service as urd_service_module

USER_ID = uuid.uuid4()
TENANT_HEADERS = {"X-Tenant-Slug": "demo"}
_ADMIN = Caller(
    user_id=USER_ID,
    keycloak_sub="kc-admin",
    is_root=True,
    permissions={"USUA_PERM_ADMIN": 5},
)


def _conn() -> AsyncMock:
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


def _override(conn: AsyncMock, caller: Caller = _ADMIN) -> None:
    async def _dep():
        yield conn

    app.dependency_overrides[get_tenant_conn] = _dep
    app.dependency_overrides[get_caller] = lambda: caller


def _urd_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "user_id": USER_ID,
        "depe_id": 120,
        "rol_id": uuid.uuid4(),
        "es_principal": False,
        "is_active": True,
        "created_at": datetime.now(tz=UTC),
    }
    base.update(kw)
    return base


# --- URD CRUD (admin) -------------------------------------------------------
@pytest.mark.asyncio
async def test_create_urd(client: AsyncClient) -> None:
    conn = _conn()
    conn.fetchrow.return_value = _urd_row(depe_id=120)
    _override(conn)

    resp = await client.post(
        f"/api/v1/auth/users/{USER_ID}/urd",
        json={"depe_id": 120, "rol_id": str(uuid.uuid4()), "es_principal": True},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    assert resp.json()["depe_id"] == 120


@pytest.mark.asyncio
async def test_create_urd_duplicate_409(client: AsyncClient) -> None:
    conn = _conn()
    conn.fetchrow.side_effect = asyncpg.UniqueViolationError("dup")
    _override(conn)

    resp = await client.post(
        f"/api/v1/auth/users/{USER_ID}/urd",
        json={"depe_id": 120, "rol_id": str(uuid.uuid4())},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_list_urd(client: AsyncClient) -> None:
    conn = _conn()
    conn.fetch.return_value = [_urd_row(), _urd_row(depe_id=130)]
    _override(conn)

    resp = await client.get(f"/api/v1/auth/users/{USER_ID}/urd", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_delete_urd_not_found(client: AsyncClient) -> None:
    conn = _conn()
    conn.execute.return_value = "DELETE 0"
    _override(conn)

    resp = await client.delete(
        f"/api/v1/auth/users/{USER_ID}/urd/{uuid.uuid4()}", headers=TENANT_HEADERS
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_urd_gate_denies_without_admin(client: AsyncClient) -> None:
    conn = _conn()
    _override(
        conn,
        Caller(
            user_id=USER_ID,
            keycloak_sub="kc-op",
            is_root=False,
            permissions={"PERM_RADI": 3},
        ),
    )
    resp = await client.get(f"/api/v1/auth/users/{USER_ID}/urd", headers=TENANT_HEADERS)
    assert resp.status_code == 403


# --- cambio de contexto (el propio usuario) --------------------------------
@pytest.mark.asyncio
async def test_context_switch(client: AsyncClient) -> None:
    conn = _conn()
    conn.fetchrow.return_value = _urd_row(depe_id=120, is_active=True)  # get()
    conn.fetchval.return_value = 120  # get_active_depe tras el switch
    conn.fetch.return_value = [_urd_row(depe_id=120)]  # available_depts
    _override(conn)

    resp = await client.post(
        "/api/v1/auth/context/switch", json={"depe_id": 120}, headers=TENANT_HEADERS
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["active_depe_id"] == 120
    assert body["user_id"] == str(USER_ID)


@pytest.mark.asyncio
async def test_context_switch_no_urd_is_400(client: AsyncClient) -> None:
    conn = _conn()
    conn.fetchrow.return_value = None  # el usuario no tiene URD en esa depe
    _override(conn)

    resp = await client.post(
        "/api/v1/auth/context/switch", json={"depe_id": 999}, headers=TENANT_HEADERS
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "no_urd_for_dependency"


# --- Canal A: auditoría inmutable de escrituras de URD ----------------------
def _patch_audit(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock_append = AsyncMock(return_value="fake-hash")
    monkeypatch.setattr(urd_service_module, "_audit", MagicMock(append=mock_append))
    monkeypatch.setattr(urd_service_module, "_AUDIT_AVAILABLE", True)
    return mock_append


@pytest.mark.asyncio
async def test_create_urd_audits_action(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_append = _patch_audit(monkeypatch)
    conn = _conn()
    urd_row = _urd_row(depe_id=120)
    conn.fetchrow.return_value = urd_row
    _override(conn)

    resp = await client.post(
        f"/api/v1/auth/users/{USER_ID}/urd",
        json={"depe_id": 120, "rol_id": str(uuid.uuid4()), "es_principal": True},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    mock_append.assert_awaited_once()
    kwargs = mock_append.call_args.kwargs
    assert kwargs["action"] == "urd.creada"
    assert kwargs["tenant_slug"] == "demo"
    assert kwargs["actor"] == str(_ADMIN.user_id)
    assert kwargs["object_ref"] == str(urd_row["id"])
    assert kwargs["payload"]["depe_id"] == 120


@pytest.mark.asyncio
async def test_create_urd_failure_leaves_no_audit_entry(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Atomicidad (mitad 1): la mutación falla (URD duplicada) → sin asiento."""
    mock_append = _patch_audit(monkeypatch)
    conn = _conn()
    conn.fetchrow.side_effect = asyncpg.UniqueViolationError("dup")
    _override(conn)

    resp = await client.post(
        f"/api/v1/auth/users/{USER_ID}/urd",
        json={"depe_id": 120, "rol_id": str(uuid.uuid4())},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 409
    mock_append.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_urd_audit_failure_reverts_mutation(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Atomicidad (mitad 2): si `_audit.append` falla, la excepción propaga
    fuera de `async with conn.transaction()` — la mutación no se confirma
    como éxito (500, no 201)."""
    mock_append = AsyncMock(side_effect=RuntimeError("audit backend down"))
    monkeypatch.setattr(urd_service_module, "_audit", MagicMock(append=mock_append))
    monkeypatch.setattr(urd_service_module, "_AUDIT_AVAILABLE", True)
    conn = _conn()
    conn.fetchrow.return_value = _urd_row(depe_id=120)
    _override(conn)

    with pytest.raises(RuntimeError, match="audit backend down"):
        await client.post(
            f"/api/v1/auth/users/{USER_ID}/urd",
            json={"depe_id": 120, "rol_id": str(uuid.uuid4())},
            headers=TENANT_HEADERS,
        )
    mock_append.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_urd_audits_action(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_append = _patch_audit(monkeypatch)
    conn = _conn()
    conn.execute.return_value = "DELETE 1"
    _override(conn)
    urd_id = uuid.uuid4()

    resp = await client.delete(
        f"/api/v1/auth/users/{USER_ID}/urd/{urd_id}", headers=TENANT_HEADERS
    )
    assert resp.status_code == 204
    mock_append.assert_awaited_once()
    kwargs = mock_append.call_args.kwargs
    assert kwargs["action"] == "urd.eliminada"
    assert kwargs["object_ref"] == str(urd_id)


@pytest.mark.asyncio
async def test_delete_urd_not_found_leaves_no_audit_entry(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_append = _patch_audit(monkeypatch)
    conn = _conn()
    conn.execute.return_value = "DELETE 0"
    _override(conn)

    resp = await client.delete(
        f"/api/v1/auth/users/{USER_ID}/urd/{uuid.uuid4()}", headers=TENANT_HEADERS
    )
    assert resp.status_code == 404
    mock_append.assert_not_awaited()


@pytest.mark.asyncio
async def test_urd_write_500_when_audit_lib_unavailable(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(urd_service_module, "_AUDIT_AVAILABLE", False)
    conn = _conn()
    _override(conn)

    resp = await client.post(
        f"/api/v1/auth/users/{USER_ID}/urd",
        json={"depe_id": 120, "rol_id": str(uuid.uuid4())},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 500
    assert resp.json()["detail"]["error"] == "auditoria_no_disponible"
