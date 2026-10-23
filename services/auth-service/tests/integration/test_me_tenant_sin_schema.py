# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`GET /auth/me` con una entidad sin schema, contra Postgres REAL (B7, F3).

Es la prueba que de verdad fija el comportamiento: `SET search_path` sobre un
schema inexistente NO falla en Postgres, y solo un Postgres real reproduce que la
primera consulta reventaba con `UndefinedTableError`. Con un doble de conexión el
500 no se ve. Los slugs llevan guion (`itest-<hex>`) y se limpian.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_optional_tenant_conn, tenant_schema
from app.main import app
from app.schemas.auth import UserInfoResponse
from tests.integration.conftest import DSN, RUN_INTEGRATION

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not RUN_INTEGRATION, reason="requiere Postgres real (RUN_INTEGRATION=1)"),
]


@pytest_asyncio.fixture
async def pool() -> AsyncGenerator[asyncpg.Pool, None]:
    p = await asyncpg.create_pool(dsn=DSN, min_size=1, max_size=2)
    app.state.db_pool = p
    try:
        yield p
    finally:
        del app.state.db_pool
        await p.close()


async def _me(slug: str) -> tuple[int, dict]:
    info = UserInfoResponse(
        user_id="kc-sub-b7", username="x", email="x@y.co", tenant_slug=slug, roles=[]
    )
    with patch(
        "app.services.auth_service.AuthService.get_current_user",
        new_callable=AsyncMock,
        return_value=info,
    ):
        from app.routers.auth import set_auth_service
        from app.services.auth_service import AuthService

        set_auth_service(AuthService())
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get(
                "/api/v1/auth/me",
                headers={"Authorization": "Bearer eyFAKE", "X-Tenant-Slug": slug},
            )
    return r.status_code, r.json()


async def test_entidad_sin_schema_responde_200_con_permisos_vacios(pool: asyncpg.Pool) -> None:
    slug = f"itest-sin-schema-{uuid.uuid4().hex[:8]}"
    schema = tenant_schema(slug)
    async with pool.acquire() as c:
        assert await c.fetchval("SELECT to_regnamespace($1::text)", schema) is None

    status, body = await _me(slug)

    assert status == 200, body
    assert body["user_id"] == "kc-sub-b7"
    assert body["permissions"] == []
    assert body["permission_levels"] == {}


async def test_el_dependency_no_deja_el_search_path_pegado(pool: asyncpg.Pool) -> None:
    """Una conexión que atiende a una entidad sin schema vuelve limpia al pool."""
    slug = f"itest-sin-schema-{uuid.uuid4().hex[:8]}"

    class _Req:
        headers = {"X-Tenant-Slug": slug}  # noqa: RUF012

        class app:  # noqa: N801
            class state:  # noqa: N801
                db_pool = pool

    gen = get_optional_tenant_conn(_Req())  # type: ignore[arg-type]
    assert await gen.__anext__() is None
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()
    async with pool.acquire() as c:
        # El valor de fábrica del servidor (`"$user", public`) o `public`: lo que no puede
        # quedar es el schema de la entidad.
        ruta = await c.fetchval("SHOW search_path")
        assert "tenant_" not in ruta and slug not in ruta and ruta in (
            "public", '"$user"', '"$user", public'
        )


async def test_entidad_con_schema_si_resuelve_la_consulta(
    pool: asyncpg.Pool, tenant_ctx: tuple[str, asyncpg.Connection]
) -> None:
    """Control positivo: con `auth_users` presente el dependency entrega la conexión."""
    slug, _ = tenant_ctx

    class _Req:
        headers = {"X-Tenant-Slug": slug}  # noqa: RUF012

        class app:  # noqa: N801
            class state:  # noqa: N801
                db_pool = pool

    gen = get_optional_tenant_conn(_Req())  # type: ignore[arg-type]
    conn = await gen.__anext__()
    assert conn is not None
    assert await conn.fetchval("SELECT count(*) FROM auth_users") == 0
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()
