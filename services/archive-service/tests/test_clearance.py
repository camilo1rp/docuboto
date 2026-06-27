# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del resolutor de clearance de archive-service (RF-SEG-08 / RF-BUS-04)."""

import uuid
from unittest.mock import AsyncMock

import asyncpg
import pytest
from fastapi import HTTPException

from app.core.clearance import CLASIFICADA, PUBLICA, check_permission, resolve_caller_clearance
from app.core.database import tenant_schema


# --- hardening de slug (defensa en profundidad ante inyección de schema) -----
def test_tenant_schema_valido() -> None:
    assert tenant_schema("demo") == "tenant_demo"
    assert tenant_schema("bogota-limpia") == "tenant_bogota_limpia"  # normaliza guiones


@pytest.mark.parametrize(
    "bad",
    ["", "Demo", "demo; DROP", 'a" OR "1', "tenant public", "../x", "demo,public"],
)
def test_tenant_schema_invalido_400(bad: str) -> None:
    with pytest.raises(HTTPException) as exc:
        tenant_schema(bad)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_clearance_none_user_is_publica() -> None:
    conn = AsyncMock()
    assert await resolve_caller_clearance(conn, None) == PUBLICA
    conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_clearance_root_is_max() -> None:
    conn = AsyncMock()
    conn.fetchrow.return_value = {"is_root": True, "max_level": None}
    assert await resolve_caller_clearance(conn, uuid.uuid4()) == CLASIFICADA


@pytest.mark.asyncio
async def test_clearance_uses_max_level() -> None:
    conn = AsyncMock()
    conn.fetchrow.return_value = {"is_root": False, "max_level": 3}
    assert await resolve_caller_clearance(conn, uuid.uuid4()) == 3


@pytest.mark.asyncio
async def test_clearance_unknown_user_is_publica() -> None:
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    assert await resolve_caller_clearance(conn, uuid.uuid4()) == PUBLICA


@pytest.mark.asyncio
async def test_clearance_fail_closed_when_rbac_tables_absent() -> None:
    conn = AsyncMock()
    conn.fetchrow.side_effect = asyncpg.UndefinedTableError("relation does not exist")
    assert await resolve_caller_clearance(conn, uuid.uuid4()) == PUBLICA


# ---------------------------------------------------------------------------
# check_permission (D-08)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_permission_none_user_is_false() -> None:
    conn = AsyncMock()
    assert await check_permission(conn, None, "USUA_PERM_EXPEDIENTE") is False
    conn.fetchval.assert_not_called()


@pytest.mark.asyncio
async def test_check_permission_granted() -> None:
    conn = AsyncMock()
    conn.fetchval.return_value = True
    assert await check_permission(conn, uuid.uuid4(), "USUA_PERM_EXPEDIENTE") is True


@pytest.mark.asyncio
async def test_check_permission_denied_user_not_found() -> None:
    conn = AsyncMock()
    conn.fetchval.return_value = None  # user not found → fail-closed
    assert await check_permission(conn, uuid.uuid4(), "USUA_PERM_EXPEDIENTE") is False


@pytest.mark.asyncio
async def test_check_permission_denied_explicitly() -> None:
    conn = AsyncMock()
    conn.fetchval.return_value = False
    assert await check_permission(conn, uuid.uuid4(), "USUA_PERM_EXPEDIENTE") is False


@pytest.mark.asyncio
async def test_check_permission_fail_closed_when_rbac_absent() -> None:
    conn = AsyncMock()
    conn.fetchval.side_effect = asyncpg.UndefinedTableError("relation does not exist")
    assert await check_permission(conn, uuid.uuid4(), "USUA_PERM_EXPEDIENTE") is False
