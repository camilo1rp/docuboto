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

from app.core.clearance import CLASIFICADA, PUBLICA, resolve_caller_clearance


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
