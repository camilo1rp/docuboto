# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La identidad que el gateway firma queda atada al método y a la ruta que REENVÍA (ADR-036).

El destino verifica contra SU petición. Se comprueba aquí, con la cabecera real que sale hacia
el upstream, que (a) verifica con el método y la ruta de la petición, incluida la forma
doblemente codificada, (b) no verifica con otra ruta ni con otro método, y (c) la query no
cuenta (el proxy la reenvía aparte, por `params`).
"""

from unittest.mock import AsyncMock

import httpx
import pytest
from orpycamcp_common import identidad

from .conftest import make_validate_response, wire_upstream

TOKEN = "Bearer valid-token"


@pytest.fixture(autouse=True)
def _secreto(monkeypatch):
    monkeypatch.setenv("IDENTITY_SIGNING_SECRET", "secreto-identidad-gateway-b10")


async def _cabeceras_al_upstream(test_client, metodo: str, url: str) -> tuple[dict, str]:
    client, http_client, _ = test_client
    http_client.get = AsyncMock(return_value=make_validate_response(valid=True))
    wire_upstream(http_client, httpx.Response(status_code=200, json={"ok": True}))
    await client.request(metodo, url, headers={"Authorization": TOKEN, "X-Tenant-Slug": "icetex"})
    kw = http_client.build_request.call_args.kwargs
    return dict(kw["headers"]), kw["url"]


@pytest.mark.asyncio
async def test_la_firma_del_gateway_verifica_con_el_metodo_y_la_ruta_reenviados(test_client):
    cab, url = await _cabeceras_al_upstream(test_client, "GET", "/api/v1/documents/?size=5&q=a")
    assert cab[identidad.CABECERA_FIRMA]
    # La URL que sale al upstream lleva la ruta tal cual; la query va aparte (params).
    ruta_reenviada = "/" + url.split("//", 1)[1].split("/", 1)[1]
    identidad.verificar(cab, metodo="GET", ruta=ruta_reenviada)
    # La query no cuenta.
    identidad.verificar(cab, metodo="GET", ruta="/api/v1/documents/?size=5&q=a")


@pytest.mark.asyncio
async def test_la_firma_del_gateway_no_sirve_en_otra_ruta_ni_otro_metodo(test_client):
    cab, _ = await _cabeceras_al_upstream(test_client, "GET", "/api/v1/documents/")
    with pytest.raises(identidad.IdentidadNoAutentica):
        identidad.verificar(cab, metodo="GET", ruta="/api/v1/documents/otra")
    with pytest.raises(identidad.IdentidadNoAutentica):
        identidad.verificar(cab, metodo="DELETE", ruta="/api/v1/documents/")


@pytest.mark.asyncio
async def test_ruta_doblemente_codificada_firma_y_verifica_igual(test_client):
    """`%2541` -> Starlette lo decodifica a `%41` -> el destino acaba viendo `A`."""
    cab, _ = await _cabeceras_al_upstream(test_client, "GET", "/api/v1/documents/x%2541")
    identidad.verificar(cab, metodo="GET", ruta="/api/v1/documents/x%41")
    identidad.verificar(cab, metodo="GET", ruta="/api/v1/documents/xA")


@pytest.mark.asyncio
async def test_ruta_que_no_estabiliza_da_404_y_no_500(test_client):
    """SEG #1: 9 niveles de `%25` -> `IdentidadNoAutentica` al firmar; debe ser el 404 del proxy."""
    client, http_client, _ = test_client
    http_client.get = AsyncMock(return_value=make_validate_response(valid=True))
    wire_upstream(http_client, httpx.Response(status_code=200, json={"ok": True}))
    url = "/api/v1/documents/%" + "25" * 12 + "41"
    resp = await client.get(url, headers={"Authorization": TOKEN, "X-Tenant-Slug": "icetex"})
    assert resp.status_code == 404
    assert resp.json()["error"] == "route_not_found"
