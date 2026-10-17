# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Una entidad no operativa se contesta con el MISMO código que el gateway (B9-N4, ADR-035).

El mcp-server resuelve la identidad con `GET /api/v1/auth/me`. Cuando la entidad está
suspendida el gateway responde `403 tenant_inactivo` (o `503 tenant_*`), pero
`resolve_identity` lo plegaba todo a `503 auth_service_error`: el cliente MCP no podía
distinguir una entidad suspendida de una caída del servicio de autenticación.
"""

from __future__ import annotations

import httpx
import pytest

from app.services.identity import IdentityError, resolve_identity


def _cliente(respuesta: httpx.Response) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: respuesta), base_url="http://gw"
    )


async def _resolver(respuesta: httpx.Response) -> IdentityError:
    async with _cliente(respuesta) as c:
        with pytest.raises(IdentityError) as exc:
            await resolve_identity(c, authorization="Bearer t", tenant_slug="demo")
    return exc.value


@pytest.mark.parametrize(
    ("estado", "error"),
    [
        (403, "tenant_inactivo"),
        (503, "tenant_no_provisionado"),
        (503, "tenant_estado_no_disponible"),
    ],
)
async def test_el_estado_de_la_entidad_se_propaga_con_su_codigo(estado: int, error: str) -> None:
    cuerpo = {"error": error, "detail": "x", "status": estado}
    exc = await _resolver(httpx.Response(estado, json=cuerpo))
    assert (exc.status, exc.code) == (estado, error)


async def test_tambien_si_el_error_viene_anidado_en_detail() -> None:
    cuerpo = {"detail": {"error": "tenant_inactivo", "detail": "x", "status": 403}}
    exc = await _resolver(httpx.Response(403, json=cuerpo))
    assert (exc.status, exc.code) == (403, "tenant_inactivo")


@pytest.mark.parametrize(
    "respuesta",
    [
        httpx.Response(503, json={"error": "otro_error"}),
        httpx.Response(500, json={"error": "tenant_inactivo"}),  # estado HTTP incoherente
        httpx.Response(403, json={"error": "forbidden"}),
        httpx.Response(503, content=b"<html>bad gateway</html>"),
        httpx.Response(503, json=["no", "es", "un", "dict"]),
    ],
    ids=["codigo_ajeno", "http_incoherente", "403_otro_motivo", "cuerpo_no_json", "json_no_dict"],
)
async def test_lo_que_no_es_estado_de_entidad_sigue_siendo_auth_service_error(
    respuesta: httpx.Response,
) -> None:
    exc = await _resolver(respuesta)
    assert (exc.status, exc.code) == (503, "auth_service_error")


async def test_el_endpoint_de_tools_refleja_la_entidad_suspendida(client_with_gateway) -> None:
    client, gateway = client_with_gateway

    async def _get(path: str, **kwargs: object) -> httpx.Response:
        return httpx.Response(
            403, json={"error": "tenant_inactivo", "detail": "x", "status": 403}
        )

    gateway.get.side_effect = _get
    resp = await client.get(
        "/api/v1/mcp/tools", headers={"Authorization": "Bearer t", "X-Tenant-Slug": "demo"}
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "tenant_inactivo"
