# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Estado de la entidad contra Postgres REAL (B9-1, ADR-035).

`estado_entidad` es una consulta a `public.tenants` y a `pg_namespace`: con un doble
no se ve que `to_regnamespace` distinga «fila activa sin schema» (que antes acababa
en `UndefinedTableError`, 500) de una entidad operativa. Los slugs llevan el prefijo
`itest-` y se limpian siempre.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
import pytest_asyncio
from orpycamcp_common.tenant import EstadoEntidad, estado_entidad

from app.core.database import tenant_schema
from app.services.auth_service import AuthService
from tests.integration.conftest import RUN_INTEGRATION

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not RUN_INTEGRATION, reason="requiere Postgres real (RUN_INTEGRATION=1)"),
]


@pytest_asyncio.fixture
async def entidad(db_conn: asyncpg.Connection) -> AsyncGenerator:
    """Fábrica de entidades desechables: `(status, provisioning, con_schema) -> slug`."""
    creadas: list[str] = []

    async def _crear(status: str, provisioning: str, con_schema: bool) -> str:
        slug = f"itest-est-{uuid.uuid4().hex[:8]}"
        creadas.append(slug)
        await db_conn.execute(
            "INSERT INTO public.tenants (slug, name, code, status, provisioning_status) "
            "VALUES ($1, $2, $3, $4, $5)",
            slug,
            slug,
            f"T{uuid.uuid4().hex[:8]}".upper(),
            status,
            provisioning,
        )
        if con_schema:
            await db_conn.execute(f'CREATE SCHEMA "{tenant_schema(slug)}"')
        return slug

    yield _crear
    for slug in creadas:
        assert slug.startswith("itest-est-"), "guardarraíl: solo se limpian entidades itest"
        await db_conn.execute(f'DROP SCHEMA IF EXISTS "{tenant_schema(slug)}" CASCADE')
        await db_conn.execute("DELETE FROM public.tenants WHERE slug = $1", slug)


@pytest.mark.parametrize(
    ("status", "provisioning", "con_schema", "esperado"),
    [
        ("active", "active", True, EstadoEntidad.OPERATIVA),
        ("suspended", "active", True, EstadoEntidad.INACTIVA),
        ("inactive", "active", True, EstadoEntidad.INACTIVA),
        ("active", "pending", True, EstadoEntidad.NO_PROVISIONADA),
        ("active", "active", False, EstadoEntidad.NO_PROVISIONADA),
    ],
)
async def test_estado_entidad_sobre_filas_reales(
    db_conn, entidad, status, provisioning, con_schema, esperado
) -> None:
    slug = await entidad(status, provisioning, con_schema)

    assert await estado_entidad(db_conn, slug) is esperado


async def test_una_entidad_sin_fila_es_inactiva(db_conn: asyncpg.Connection) -> None:
    assert await estado_entidad(db_conn, f"itest-sin-fila-{uuid.uuid4().hex[:8]}") is (
        EstadoEntidad.INACTIVA
    )


async def test_un_slug_mal_formado_es_inactivo(db_conn: asyncpg.Connection) -> None:
    assert await estado_entidad(db_conn, 'x"; drop schema public;--') is EstadoEntidad.INACTIVA


async def _validar(db_conn: asyncpg.Connection, slug: str):
    claims = {
        "user_id": f"kc-{uuid.uuid4().hex[:8]}",
        "username": "u",
        "email": "u@e.co",
        "tenant_slug": slug,
        "roles": [],
        "exp": 4102444800,
    }
    with patch(
        "app.services.auth_service.validate_token", new=AsyncMock(return_value=claims)
    ):
        return claims, await AuthService().validate("tok", db_conn)


async def test_validate_de_una_entidad_suspendida_no_aprovisiona_usuarios(
    db_conn: asyncpg.Connection, entidad
) -> None:
    """Sin el estado primero, el alta perezosa creaba `auth_users` dentro de una
    entidad suspendida."""
    from migrations import runner

    slug = await entidad("suspended", "active", True)
    schema = tenant_schema(slug)
    for path in runner._sql_files(runner.TENANT_DIR):
        async with db_conn.transaction():
            await db_conn.execute(f'SET LOCAL search_path = "{schema}", public')
            await db_conn.execute(path.read_text())

    claims, resp = await _validar(db_conn, slug)

    assert resp.valid is True
    assert resp.tenant_estado == "inactiva"
    assert resp.user_id == claims["user_id"]  # el `sub` crudo: no se resolvió ni se creó nada
    assert await db_conn.fetchval(f'SELECT count(*) FROM "{schema}".auth_users') == 0


async def test_validate_de_una_entidad_operativa_dice_operativa(
    db_conn: asyncpg.Connection, entidad
) -> None:
    from migrations import runner

    slug = await entidad("active", "active", True)
    schema = tenant_schema(slug)
    for path in runner._sql_files(runner.TENANT_DIR):
        async with db_conn.transaction():
            await db_conn.execute(f'SET LOCAL search_path = "{schema}", public')
            await db_conn.execute(path.read_text())

    _, resp = await _validar(db_conn, slug)

    assert resp.tenant_estado == "operativa"
    assert await db_conn.fetchval(f'SELECT count(*) FROM "{schema}".auth_users') == 1

