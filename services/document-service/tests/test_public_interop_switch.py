# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El interruptor de la interoperabilidad pública anónima (E11).

Estos endpoints cosechan el registro de correspondencia y sirven los BYTES de los
anexos sin autenticación, y su único recorte es `nivel_seguridad = 1`… que es el
DEFAULT de la columna y del schema de entrada. Publicar eso es una decisión de la
entidad (esquema de publicación, Ley 1712 art. 12), no un efecto secundario de
desplegar el software.

**Estos tests NO configuran el interruptor**: comprueban precisamente el valor por
defecto. Si alguien lo cambiara a `True`, se ponen en rojo — que es el punto.
"""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import app

pytestmark = pytest.mark.asyncio

_RUTAS = [
    "/api/v1/public/oai/icetex?verb=Identify",
    "/api/v1/public/oai/icetex?verb=ListRecords&metadataPrefix=oai_dc",
    "/api/v1/public/cmis/icetex",
    "/api/v1/public/cmis/icetex/root?cmisselector=children",
    "/api/v1/public/cmis/icetex/root?cmisselector=content&objectId=anexo-1",
]


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


def test_el_default_es_apagado() -> None:
    """El default vive en `core/config.py` y es lo que hereda un despliegue nuevo.

    Se comprueba el ajuste directamente, además de su efecto: si alguien invirtiera
    el default, los tests de ruta de abajo también fallarían, pero este dice por qué.
    """
    assert settings.public_interop_enabled is False


@pytest.mark.parametrize("ruta", _RUTAS)
async def test_apagado_responde_404(client: AsyncClient, ruta: str) -> None:
    """404, no 403.

    Un 403 confirmaría que detrás hay un servicio de cosecha apagado. El 404 es
    indistinguible de una ruta que no existe — el mismo criterio de
    omisión-indistinguible con el que estos endpoints ocultan los reservados.
    """
    response = await client.get(ruta)

    assert response.status_code == 404


@pytest.mark.parametrize("ruta", _RUTAS)
async def test_apagado_no_toca_la_base_de_datos(client: AsyncClient, ruta: str) -> None:
    """El rechazo ocurre en la dependencia del router, antes de adquirir conexión.

    `app.state.db_pool` no existe en este test (no hay lifespan): si el guard no
    cortara antes, la petición reventaría con `AttributeError` → 500, no 404. Que
    la respuesta sea 404 es la prueba de que no se llegó a la base.
    """
    assert not hasattr(app.state, "db_pool")

    response = await client.get(ruta)

    assert response.status_code == 404


@pytest.mark.parametrize("ruta", _RUTAS)
async def test_encendido_ya_no_da_404_de_ruta_inexistente(
    client: AsyncClient, ruta: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control positivo: con el interruptor encendido el guard deja pasar.

    Sin este test, los de arriba pasarían igual si el 404 viniera de que la ruta no
    está montada en absoluto — es decir, no probarían el interruptor, solo su
    ausencia. Aquí la petición llega al handler y muere más adelante por falta de
    `db_pool`: en OAI la excepción se propaga, y en CMIS `_acquire` la captura y la
    traduce a 400. Cualquiera de las dos cosas sirve de señal; lo único que NO puede
    ocurrir es un 404, que es la firma del guard.
    """
    from app.core import config as cfg_module

    monkeypatch.setattr(cfg_module.settings, "public_interop_enabled", True)

    try:
        response = await client.get(ruta)
    except AttributeError as exc:  # OAI: `app.state.db_pool` no existe sin lifespan
        assert "db_pool" in str(exc)
    else:
        assert response.status_code != 404
