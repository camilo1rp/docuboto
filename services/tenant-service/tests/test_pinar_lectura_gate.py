# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Las LECTURAS del PINAR también exigen `USUA_PERM_ADMIN` (2026-08-28).

Hasta esta fecha las 20+ escrituras del plan estaban gateadas y los 11 GET no:
cualquier funcionario del tenant leía el plan entero —visión, aspectos críticos,
priorización de riesgos, objetivos, proyectos y su seguimiento— escribiendo la
URL, mientras la barra lateral y las tarjetas de `/admin` le escondían la
pantalla. Esconder algo y servirlo a la vez no es una decisión, es un descuido
con dos caras.

**Este guardián se escribe por ADMISIÓN, no por lista**: recorre las rutas que
el `APIRouter` declara y exige el 403 en todas. Un endpoint nuevo que alguien
añada mañana entra solo en la comprobación; una lista escrita a mano se queda
atrás en silencio, que es exactamente como se llegó hasta aquí.
"""

import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from app.core.database import get_tenant_conn
from app.main import app
from app.routers import pinar

_HDRS = {"X-User-Id": str(uuid.uuid4()), "X-Tenant-Slug": "demo"}


def _rutas_de_lectura() -> list[str]:
    """Toda ruta GET del router del PINAR, con los parámetros de camino
    rellenos con un UUID cualquiera: el gate corta antes de mirarlos."""
    rutas = []
    for route in pinar.router.routes:
        if not isinstance(route, APIRoute) or "GET" not in route.methods:
            continue
        camino = route.path
        for nombre in route.param_convertors:
            camino = camino.replace("{" + nombre + "}", str(uuid.uuid4()))
        rutas.append(camino)
    return sorted(rutas)


@pytest_asyncio.fixture(scope="function")
async def client_conn() -> AsyncGenerator[tuple[AsyncClient, AsyncMock], None]:
    conn = AsyncMock()
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    conn.fetchval.return_value = False  # permiso DENEGADO: es lo que se prueba

    async def override() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = override
    # `check_permission` deja asiento de la DENEGACIÓN en la cadena inmutable;
    # aquí se prueba el gate, no el hash (eso vive en orpycamcp_common).
    with patch("app.core.authz.audit.append", new=AsyncMock(return_value="h")):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac, conn
    app.dependency_overrides.clear()


def test_el_censo_no_esta_vacio() -> None:
    """Anti-vacuidad: si `_rutas_de_lectura()` devolviera [] —un cambio de
    nombre del router, un import que falla— el test parametrizado de abajo
    pasaría sin comprobar NADA. Los 11 GET son los que había el día del
    cambio; el `>=` deja crecer el router sin tocar este número."""
    rutas = _rutas_de_lectura()
    assert len(rutas) >= 11, rutas


@pytest.mark.asyncio
@pytest.mark.parametrize("ruta", _rutas_de_lectura())
async def test_toda_lectura_del_pinar_exige_el_permiso(
    ruta: str, client_conn: tuple[AsyncClient, AsyncMock]
) -> None:
    client, conn = client_conn
    resp = await client.get(ruta, headers=_HDRS)
    assert resp.status_code == 403, f"{ruta} respondió {resp.status_code}"
    assert resp.json()["detail"]["error"] == "forbidden"
    # El gate corta ANTES de la lógica: no se consultó ningún dato del plan.
    conn.fetch.assert_not_called()
    conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("ruta", _rutas_de_lectura())
async def test_sin_identidad_tampoco(
    ruta: str, client_conn: tuple[AsyncClient, AsyncMock]
) -> None:
    """Fail-closed sobre la identidad: sin `X-User-Id` no hay usuario que
    resolver, así que se deniega aunque la BD «tuviera» el permiso."""
    client, conn = client_conn
    conn.fetchval.return_value = True
    resp = await client.get(ruta, headers={"X-Tenant-Slug": "demo"})
    assert resp.status_code == 403, f"{ruta} respondió {resp.status_code}"
