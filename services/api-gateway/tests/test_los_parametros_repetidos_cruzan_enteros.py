# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Un parámetro repetido llega entero al servicio.

El proxy reenviaba `params=dict(request.query_params)`. La cadena de consulta es
un MULTIDICT, y `dict()` se queda con el ÚLTIMO valor de cada clave: comprobado
contra Starlette, `?status=distributed&status=in_progress` salía de aquí como
`{'status': 'in_progress'}`.

**El modo de fallo es el peor que hay**: 200, sin error, y con menos filas. Quien
pidiera dos estados recibiría uno y creería que ese es el total. Y deja
INALCANZABLE desde fuera cualquier parámetro repetible que un servicio declare,
por bien que lo declare — el frontend y el mcp-server hablan siempre por aquí.

**Por qué ninguna suite lo cazaba, que es la parte que importa**: los tests de
cada servicio golpean su propia app, donde el multidict llega intacto. El
defecto vivía en el SALTO, que no es de nadie. Por eso este test vive en el
gateway y mira lo que se reenvía, no lo que contesta un servicio.
"""

from unittest.mock import AsyncMock

import httpx
import pytest

from tests.conftest import make_validate_response, wire_upstream

pytestmark = pytest.mark.asyncio


async def _forward(client, http_client, query: str):
    """Hace una petición por el proxy y devuelve los `params` reenviados."""
    http_client.get = AsyncMock(return_value=make_validate_response(valid=True))
    wire_upstream(
        http_client,
        httpx.Response(status_code=200, json={"items": []}, headers={"content-type": "application/json"}),
    )
    resp = await client.get(
        f"/api/v1/dependencias/?{query}",
        headers={"Authorization": "Bearer valid-token"},
    )
    assert resp.status_code == 200
    return http_client.build_request.call_args.kwargs["params"]


async def test_una_clave_repetida_cruza_con_todos_sus_valores(test_client):
    client, http_client, _ = test_client

    params = await _forward(client, http_client, "status=distributed&status=in_progress&size=5")

    # Se compara como lista de pares y no como dict: convertirlo a dict aquí
    # reproduciría el propio defecto dentro del test.
    pares = list(params)
    assert ("status", "distributed") in pares
    assert ("status", "in_progress") in pares
    assert ("size", "5") in pares


async def test_el_orden_de_los_valores_se_conserva(test_client):
    """No es cosmético: un servicio puede darle sentido al orden —el primero
    como preferente, por ejemplo— y reordenar sería decidir por él."""
    client, http_client, _ = test_client

    params = await _forward(client, http_client, "estado=a&estado=b&estado=c")

    assert [v for k, v in params if k == "estado"] == ["a", "b", "c"]


async def test_una_clave_sin_repetir_sigue_cruzando_igual(test_client):
    """Control positivo: el caso normal, que es el de casi todas las llamadas,
    no puede haberse roto al arreglar el otro."""
    client, http_client, _ = test_client

    params = await _forward(client, http_client, "page=2&size=20")

    assert dict(params) == {"page": "2", "size": "20"}


async def test_sin_cadena_de_consulta_no_se_inventa_nada(test_client):
    client, http_client, _ = test_client

    params = await _forward(client, http_client, "")

    assert list(params) == []
