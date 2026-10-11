# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`check_any_permission` (gate any-of) contra Postgres REAL.

El SQL usa `p.nombre = ANY($2::text[])`: lo que un mock no prueba es que el
adaptador de asyncpg pase la lista como arreglo de texto y que la semántica sea
«basta UNO» (no «todos»), con ROOT que pasa, nivel CRUD respetado y fail-closed.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest
from orpycamcp_common.rbac import check_any_permission

from tests.integration.conftest import RUN_INTEGRATION

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not RUN_INTEGRATION, reason="requiere Postgres real (RUN_INTEGRATION=1)"),
]

_ANY = ("PERM_RADI", "USUA_PERM_ADMIN")


async def _usuario(
    conn: asyncpg.Connection, nombre: str, *, permisos: dict[str, int], root: bool = False
) -> uuid.UUID:
    uid = await conn.fetchval(
        "INSERT INTO auth_users (username, is_root) VALUES ($1, $2) RETURNING id", nombre, root
    )
    gid = await conn.fetchval("INSERT INTO auth_groups (name) VALUES ($1) RETURNING id", nombre)
    await conn.execute("INSERT INTO auth_memberships (user_id, group_id) VALUES ($1, $2)", uid, gid)
    for perm, crud in permisos.items():
        pid = await conn.fetchval(
            "INSERT INTO auth_permissions (nombre) VALUES ($1) "
            "ON CONFLICT (nombre) DO UPDATE SET nombre = EXCLUDED.nombre RETURNING id",
            perm,
        )
        await conn.execute(
            "INSERT INTO auth_group_permissions (group_id, permission_id, crud) VALUES ($1, $2, $3)",
            gid, pid, crud,
        )
    return uid  # type: ignore[no-any-return]


async def test_basta_uno_de_los_dos(tenant_ctx: tuple[str, asyncpg.Connection]) -> None:
    _, conn = tenant_ctx
    solo_radi = await _usuario(conn, "SOLO_RADI", permisos={"PERM_RADI": 1})
    solo_admin = await _usuario(conn, "SOLO_ADMIN", permisos={"USUA_PERM_ADMIN": 1})
    ninguno = await _usuario(conn, "NINGUNO", permisos={"USUA_PERM_CONSULTA": 5})

    assert await check_any_permission(conn, solo_radi, _ANY) is True
    assert await check_any_permission(conn, solo_admin, _ANY) is True
    assert await check_any_permission(conn, ninguno, _ANY) is False


async def test_root_pasa_y_el_nivel_se_respeta(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    _, conn = tenant_ctx
    root = await _usuario(conn, "EL_ROOT", permisos={}, root=True)
    corto = await _usuario(conn, "CORTO", permisos={"PERM_RADI": 1})

    assert await check_any_permission(conn, root, _ANY) is True
    assert await check_any_permission(conn, corto, _ANY, min_crud=1) is True
    assert await check_any_permission(conn, corto, _ANY, min_crud=3) is False


async def test_fail_closed(tenant_ctx: tuple[str, asyncpg.Connection]) -> None:
    _, conn = tenant_ctx
    uid = await _usuario(conn, "ALGUIEN", permisos={"PERM_RADI": 5})
    assert await check_any_permission(conn, None, _ANY) is False
    assert await check_any_permission(conn, uuid.uuid4(), _ANY) is False
    assert await check_any_permission(conn, uid, ()) is False
