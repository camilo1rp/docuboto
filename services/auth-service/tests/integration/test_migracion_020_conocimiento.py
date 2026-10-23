# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Migración tenant 020 (PERM_CONOCIMIENTO) contra Postgres REAL.

Un mock no prueba el reparto: cada grupo que existe al aplicar la 020 recibe el permiso
con el nivel que sale de SU propio máximo (solo lectura -> 1, escribe -> 2), y un grupo
creado DESPUÉS no recibe nada. El schema desechable de ``tenant_ctx`` ya tiene la 020
aplicada, así que se reconstruye el estado «antes» borrando la concesión y se reaplica
el archivo.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest
from orpycamcp_common.rbac import check_permission

from migrations import runner
from tests.integration.conftest import RUN_INTEGRATION

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not RUN_INTEGRATION, reason="requiere Postgres real (RUN_INTEGRATION=1)"),
]

_PERM = "PERM_CONOCIMIENTO"


def _sql_020() -> str:
    return next(p for p in runner._sql_files(runner.TENANT_DIR) if p.name.startswith("020_")).read_text()


async def _grupo(conn: asyncpg.Connection, nombre: str, permisos: dict[str, int]) -> tuple[uuid.UUID, uuid.UUID]:
    gid = await conn.fetchval("INSERT INTO auth_groups (name) VALUES ($1) RETURNING id", nombre)
    uid = await conn.fetchval("INSERT INTO auth_users (username) VALUES ($1) RETURNING id", nombre)
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
    return gid, uid


async def _crud(conn: asyncpg.Connection, gid: uuid.UUID) -> int | None:
    return await conn.fetchval(
        "SELECT gp.crud FROM auth_group_permissions gp JOIN auth_permissions p ON p.id = gp.permission_id "
        "WHERE gp.group_id = $1 AND p.nombre = $2",
        gid, _PERM,
    )


async def test_el_reparto_sale_del_nivel_de_cada_grupo(tenant_ctx: tuple[str, asyncpg.Connection]) -> None:
    _, conn = tenant_ctx
    # Estado «antes de la 020»: el permiso existe pero nadie lo tiene.
    await conn.execute(
        "DELETE FROM auth_group_permissions USING auth_permissions p "
        "WHERE p.id = permission_id AND p.nombre = $1", _PERM,
    )
    solo_lectura, u_lee = await _grupo(conn, "LECTORES", {"USUA_PERM_CONSULTA": 1})
    escribe, u_esc = await _grupo(conn, "ESCRIBEN", {"PERM_RADI": 3, "USUA_PERM_CONSULTA": 1})
    vacio, _ = await _grupo(conn, "SIN_PERMISOS", {})
    en_cero, _ = await _grupo(conn, "TODO_EN_CERO", {"USUA_PERM_CONSULTA": 0})

    await conn.execute(_sql_020())

    assert await _crud(conn, solo_lectura) == 1  # consulta, no acepta
    assert await _crud(conn, escribe) == 2  # tope en 2 aunque tenga 3
    assert await _crud(conn, vacio) is None  # S3: un grupo sin permisos no gana acceso a la IA
    assert await _crud(conn, en_cero) is None  # ni uno cuyos permisos están todos en 0

    # check_permission REAL concede/niega con esos niveles (control positivo y negativo).
    assert await check_permission(conn, u_lee, _PERM, 1) is True
    assert await check_permission(conn, u_lee, _PERM, 2) is False
    assert await check_permission(conn, u_esc, _PERM, 2) is True


async def test_un_grupo_creado_despues_no_recibe_el_permiso(tenant_ctx: tuple[str, asyncpg.Connection]) -> None:
    _, conn = tenant_ctx
    posterior, uid = await _grupo(conn, "POSTERIOR", {"PERM_RADI": 3})
    assert await _crud(conn, posterior) is None
    assert await check_permission(conn, uid, _PERM, 1) is False


async def test_reaplicar_no_pisa_un_ajuste_del_administrador(tenant_ctx: tuple[str, asyncpg.Connection]) -> None:
    _, conn = tenant_ctx
    gid, _ = await _grupo(conn, "AJUSTADO", {"PERM_RADI": 3})
    await conn.execute(_sql_020())
    await conn.execute(
        "UPDATE auth_group_permissions SET crud = 0 WHERE group_id = $1 AND permission_id = "
        "(SELECT id FROM auth_permissions WHERE nombre = $2)", gid, _PERM,
    )
    await conn.execute(_sql_020())
    assert await _crud(conn, gid) == 0
