# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El gateway autorizaba sobre una ruta y enviaba otra (salto de segmentos `..`).

**Por qué estos tests NO usan `AsyncClient`.** httpx normaliza la ruta al
construir la petición —`httpx.Request('GET', '.../dependencias/../tenants/')`
sale ya como `/api/v1/tenants/`—, así que un test escrito con el cliente
*normaliza el ataque antes de lanzarlo* y pasa en verde contra un gateway
vulnerable. Estaría comprobando httpx, no el gateway.

Aquí se invoca la aplicación ASGI con un `scope` construido a mano, que es
exactamente lo que uvicorn entrega: la ruta ya percent-decodificada y SIN
normalizar. Es el único punto desde el que el defecto es observable.
"""

from typing import Any
from urllib.parse import unquote

import pytest

from app.main import app
from app.middleware.path_guard import has_dot_segments

# Solo los tests que invocan la app ASGI son async; los de `has_dot_segments` son
# sincronos, asi que la marca va por test y no global (evita el warning de
# pytest-asyncio sobre funciones no-async marcadas).
asyncio_test = pytest.mark.asyncio


async def _send_raw_path(target: str) -> dict[str, Any]:
    """Invoca la app ASGI con *target* tal como llegaría por la red.

    Reproduce lo que hace uvicorn con la línea de petición: `raw_path` son los
    bytes literales y `scope["path"]` es esa misma ruta **percent-decodificada**.
    Esa decodificación no es un detalle del test — es la razón por la que basta
    con mirar segmentos `..` para cubrir también `%2e%2e`.
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": unquote(target),
        "raw_path": target.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"test")],
        "client": ("10.0.0.1", 12345),
        "server": ("test", 80),
    }
    mensajes: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        mensajes.append(message)

    await app(scope, receive, send)
    return mensajes[0]


# ---------------------------------------------------------------------------
# El salto de ruta
# ---------------------------------------------------------------------------


@asyncio_test
async def test_salto_a_tenants_rechazado() -> None:
    """`/api/v1/dependencias/../tenants/` no puede alcanzar el registro de instituciones.

    Este es el ataque concreto: `/api/v1/tenants/` está deliberadamente FUERA de
    la tabla de ruteo (remediación de un hallazgo Crítico previo), pero el
    ruteo casaba el prefijo `/api/v1/dependencias/` y httpx normalizaba después,
    entregando `/api/v1/tenants/` al upstream.
    """
    respuesta = await _send_raw_path("/api/v1/dependencias/../tenants/")

    assert respuesta["status"] == 400


@asyncio_test
async def test_salto_bajo_prefijo_publico_rechazado() -> None:
    """`/api/v1/public/../documents/` salta ADEMÁS la autenticación.

    `_is_public` casa por prefijo crudo, así que este salto se colaba *antes* de
    validar el JWT: el 401 que debería llegar nunca llegaba. Por eso el guard va
    montado como middleware más externo y no dentro del router de proxy.
    """
    respuesta = await _send_raw_path("/api/v1/public/../documents/")

    assert respuesta["status"] == 400


@asyncio_test
async def test_salto_percent_encoded_rechazado() -> None:
    """`%2e%2e` es el mismo ataque: uvicorn lo entrega ya decodificado como `..`.

    Comprobar solo la cadena literal `..` dejaría fuera esta variante, que es la
    que usan las herramientas automáticas.
    """
    respuesta = await _send_raw_path("/api/v1/dependencias/%2e%2e/tenants/")

    assert respuesta["status"] == 400


@asyncio_test
async def test_salto_en_storage_rechazado() -> None:
    """`/api/v1/storage/upload/../files/{id}/replace` sustituía el contenido de un anexo."""
    respuesta = await _send_raw_path(
        "/api/v1/storage/upload/../files/11111111-1111-1111-1111-111111111111/replace"
    )

    assert respuesta["status"] == 400


# ---------------------------------------------------------------------------
# Que no muerda al tráfico legítimo
# ---------------------------------------------------------------------------


@asyncio_test
async def test_ruta_legitima_llega_a_la_autenticacion() -> None:
    """El guard no puede convertirse en un 400 sobre tráfico normal.

    Un 401 aquí es la prueba POSITIVA de que el guard dejó pasar la petición y
    la cadena siguió hasta el middleware de autenticación: si el guard rechazara
    de más, este test vería un 400 y no un 401. Se elige una ruta que se resuelve
    ANTES de tocar `app.state`, para que el test mida el guard y no el cableado
    del cliente HTTP.
    """
    respuesta = await _send_raw_path("/api/v1/documents/")

    assert respuesta["status"] == 401


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/auth/token",
        "/api/v1/documents/",
        "/api/v1/expedientes/11111111-1111-1111-1111-111111111111",
        # Barra final y barra doble: `posixpath.normpath` las cambiaría, pero
        # ninguna es un salto de ruta. Comparar contra `normpath` habría dado
        # falsos positivos justo aquí.
        "/api/v1/trd/",
        "/api/v1//trd/",
        # Un segmento que CONTIENE puntos pero no ES un punto.
        "/api/v1/documents/informe..final.pdf",
    ],
)
def test_rutas_legitimas_no_tienen_segmentos_punto(path: str) -> None:
    assert has_dot_segments(path) is False


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/dependencias/../tenants/",
        "/api/v1/./documents/",
        "/..",
        "/api/v1/a/b/../../tenants",
    ],
)
def test_rutas_con_salto_se_detectan(path: str) -> None:
    assert has_dot_segments(path) is True
