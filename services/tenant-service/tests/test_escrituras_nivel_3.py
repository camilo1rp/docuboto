# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Toda ESCRITURA de dependencias, catálogos y PINAR exige nivel CRUD 3.

Antes bastaba `USUA_PERM_ADMIN` a nivel 1 (lectura). Guardián por ADMISIÓN: recorre
las rutas no-GET de los tres routers, de modo que una escritura nueva sin
`min_crud=3` cae sola. El mock de `fetchval` responde al gate como lo haría la BD:
concede si el nivel del usuario alcanza el `min_crud` exigido (último parámetro).
"""

import uuid
from collections.abc import AsyncGenerator, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from app.core.database import get_tenant_conn
from app.main import app
from app.routers import catalogos, dependencias, pinar

_HDRS = {"X-User-Id": str(uuid.uuid4()), "X-Tenant-Slug": "demo"}


def _escrituras() -> list[tuple[str, str]]:
    out = []
    for router in (dependencias.router, catalogos.router, pinar.router):
        for route in router.routes:
            if not isinstance(route, APIRoute):
                continue
            camino = route.path
            for nombre in route.param_convertors:
                valor = "plantillas" if nombre == "catalogo" else str(uuid.uuid4())
                camino = camino.replace("{" + nombre + "}", valor)
            for metodo in route.methods - {"GET", "HEAD", "OPTIONS"}:
                out.append((metodo, camino))
    return sorted(out)


def _gate(nivel: int) -> Callable[..., object]:
    def _f(sql: str, *a: object) -> object:
        if "auth_users" in sql and isinstance(a[-1], int):
            return a[-1] <= nivel
        return None

    return _f


@pytest_asyncio.fixture(scope="function")
async def client_conn() -> AsyncGenerator[tuple[AsyncClient, AsyncMock], None]:
    conn = AsyncMock()
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    conn.transaction = MagicMock(side_effect=Exception("no debe alcanzarse"))

    async def override() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = override
    with patch("app.core.audit._audit.append", new=AsyncMock(return_value="h")):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac, conn
    app.dependency_overrides.clear()


def test_hay_escrituras_que_comprobar() -> None:
    assert len(_escrituras()) >= 25


@pytest.mark.asyncio
@pytest.mark.parametrize(("metodo", "ruta"), _escrituras())
async def test_nivel_2_recibe_403_en_toda_escritura(
    client_conn: tuple[AsyncClient, AsyncMock], metodo: str, ruta: str
) -> None:
    client, conn = client_conn
    conn.fetchval.side_effect = _gate(2)
    resp = await client.request(metodo, ruta, json={}, headers=_HDRS)
    assert resp.status_code == 403
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(("metodo", "ruta"), _escrituras())
async def test_nivel_3_pasa_el_gate_en_toda_escritura(
    client_conn: tuple[AsyncClient, AsyncMock], metodo: str, ruta: str
) -> None:
    client, conn = client_conn
    conn.fetchval.side_effect = _gate(3)
    resp = await client.request(metodo, ruta, json={}, headers=_HDRS)
    assert resp.status_code != 403
