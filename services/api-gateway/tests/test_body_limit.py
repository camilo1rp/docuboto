# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El gateway bufferizaba el cuerpo entero en memoria y sin ningún tope.

`await request.body()` en la subida y `.content` en la bajada: el cuerpo se
materializaba completo en el gateway (y storage hacía una segunda copia del de
subida), con `memory: 1G` por réplica y sin límite de tamaño en ninguna capa. Una
subida grande no daba un error: mataba el contenedor.

El tope y el flujo son **una sola corrección, no dos**. Subir
`BODY_SIZE_LIMIT` en el frontend sin esto habría cambiado un 413 espurio por un
OOM real.
"""

from unittest.mock import AsyncMock

import httpx
import pytest

from app.main import app
from tests.conftest import make_validate_response, wire_upstream

pytestmark = pytest.mark.asyncio

_AUTH = {"Authorization": "Bearer tok", "X-Tenant-Slug": "icetex"}


def _upstream_ok() -> httpx.Response:
    return httpx.Response(
        status_code=201,
        json={"id": "abc"},
        headers={"content-type": "application/json"},
    )


# ---------------------------------------------------------------------------
# El tope
# ---------------------------------------------------------------------------


async def test_content_length_por_encima_del_tope_es_413(test_client) -> None:
    """Se rechaza ANTES de leer un solo byte del cuerpo.

    Cuando el cliente declara el tamaño, aceptar la subida entera para después
    rechazarla es pagar el coste completo del ataque antes de defenderse.
    """
    client, http_client, _ = test_client
    http_client.get = _validate_ok(http_client)
    wire_upstream(http_client, _upstream_ok())

    resp = await client.post(
        "/api/v1/storage/upload",
        content=b"x" * 100,
        headers={**_AUTH, "content-length": str(10**12)},
    )

    assert resp.status_code == 413
    assert resp.json()["error"] == "request_entity_too_large"
    http_client.send.assert_not_called(), "no debe llegar nada al upstream"


async def test_cuerpo_chunked_por_encima_del_tope_es_413(test_client) -> None:
    """Sin `Content-Length` el tope también muerde.

    `Transfer-Encoding: chunked` no declara tamaño, y quien quiere agotar la
    memoria del proceso es justamente quien lo omite: un chequeo que solo mirase
    la cabecera sería un control que el atacante desactiva no enviándola.
    """
    client, http_client, _ = test_client
    http_client.get = _validate_ok(http_client)
    wire_upstream(http_client, _upstream_ok())
    app.state.max_request_body_bytes = 1024

    async def _cuerpo():
        for _ in range(4):
            yield b"y" * 512  # 2 KiB en total, sin Content-Length

    resp = await client.post("/api/v1/storage/upload", content=_cuerpo(), headers=_AUTH)

    assert resp.status_code == 413
    assert resp.json()["error"] == "request_entity_too_large"


async def test_cuerpo_dentro_del_tope_pasa(test_client) -> None:
    """Control positivo: sin esto, un 413 constante también pasaría los tests de arriba."""
    client, http_client, _ = test_client
    http_client.get = _validate_ok(http_client)
    wire_upstream(http_client, _upstream_ok())
    app.state.max_request_body_bytes = 1024

    resp = await client.post(
        "/api/v1/storage/upload",
        content=b"z" * 512,
        headers=_AUTH,
    )

    assert resp.status_code == 201
    http_client.send.assert_called_once()


# ---------------------------------------------------------------------------
# El flujo
# ---------------------------------------------------------------------------


async def test_el_cuerpo_de_subida_no_se_materializa(test_client) -> None:
    """El `content` que recibe httpx es un iterador, no `bytes`.

    Es la diferencia entre transmitir por trozos y copiar el fichero entero en
    memoria. Se comprueba la forma del argumento porque el consumo real de memoria
    no es observable desde un test unitario — y se dice así en vez de fingir que
    se mide.
    """
    client, http_client, _ = test_client
    http_client.get = _validate_ok(http_client)
    wire_upstream(http_client, _upstream_ok())

    await client.post("/api/v1/storage/upload", content=b"a" * 4096, headers=_AUTH)

    enviado = http_client.build_request.call_args.kwargs["content"]
    assert not isinstance(enviado, bytes | bytearray | str)
    assert hasattr(enviado, "__aiter__"), "el cuerpo debe viajar como flujo"


async def test_la_respuesta_se_cierra_al_terminar(test_client) -> None:
    """La respuesta del upstream se cierra cuando el cliente acaba de leer.

    Con `stream=True` la conexión sigue tomada del pool hasta el `aclose`. Sin el
    `BackgroundTask` que lo hace, cada descarga retendría una conexión y el pool se
    agotaría al cabo de un rato — un fallo que aparece bajo carga, no en pruebas.
    """
    client, http_client, _ = test_client
    http_client.get = _validate_ok(http_client)
    wire_upstream(http_client, _upstream_ok())

    await client.post("/api/v1/storage/upload", content=b"a", headers=_AUTH)

    http_client.send.return_value.aclose.assert_awaited()


async def test_content_encoding_y_disposition_llegan_al_cliente(test_client) -> None:
    """Al reenviar bytes crudos, `content-encoding` deja de ser opcional.

    Se reenvía sin descomprimir (eso es lo que evita materializar la respuesta),
    así que sin esa cabecera el cliente recibiría bytes gzip anunciados como texto
    plano. `content-disposition` viaja por el mismo motivo práctico: sin ella el
    navegador inventa el nombre del anexo descargado.
    """
    client, http_client, _ = test_client
    http_client.get = _validate_ok(http_client)
    import gzip

    comprimido = gzip.compress(b"cuerpo")
    wire_upstream(
        http_client,
        httpx.Response(
            status_code=200,
            # `content` se le da COMPRIMIDO porque httpx decodifica al construir
            # la respuesta: con el cuerpo en claro y la cabecera gzip, el objeto
            # ni siquiera llega a existir. Así `content-length` cuenta además los
            # bytes que de verdad viajan, que es lo coherente con el reenvío crudo.
            content=comprimido,
            headers={
                "content-type": "application/pdf",
                "content-encoding": "gzip",
                "content-disposition": 'attachment; filename="anexo.pdf"',
            },
        ),
        # Bytes gzip REALES en el flujo: el gateway los reenvía crudos y el cliente
        # los descomprime. Con bytes falsos el test moriría en el decodificador de
        # httpx y estaría probando httpx, no el gateway.
        raw=comprimido,
    )

    resp = await client.get("/api/v1/documents/", headers=_AUTH)

    assert resp.headers["content-encoding"] == "gzip"
    assert "anexo.pdf" in resp.headers["content-disposition"]


def _validate_ok(http_client) -> AsyncMock:
    """`GET /auth/validate` en verde — el middleware de JWT lo llama en toda ruta."""
    return AsyncMock(return_value=make_validate_response(valid=True))
