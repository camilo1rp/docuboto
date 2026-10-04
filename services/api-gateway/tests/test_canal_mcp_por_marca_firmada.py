# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""AREA-11-04: el canal 'mcp' se prueba con una marca firmada, no se declara.

Lo que el mcp-server hace en nombre de una persona entra por `/api/v1/<dominio>` y
se asentaba como 'api'. El gateway solo asienta 'mcp' por esa vía si la petición
trae `X-Origen-Canal` con firma válida para ESE tenant y ESE token; sin marca, con
firma mala, vencida o de otro token, es 'api' — y un cliente sin el secreto no puede
fabricarla.
"""

import time
from unittest.mock import AsyncMock

import httpx
import pytest
from orpycamcp_common import identidad

from app.middleware.auth import derive_canal

from .conftest import make_validate_response, wire_upstream

SECRETO = "secreto-de-prueba-del-origen"
TOKEN = "Bearer valid-token"


@pytest.fixture(autouse=True)
def _secreto(monkeypatch):
    monkeypatch.setenv("MCP_ORIGIN_SECRET", SECRETO)


def _marca(**kw) -> str:
    args = {"tenant": "icetex", "authorization": TOKEN} | kw
    return identidad.firmar_origen("mcp", **args)[identidad.CABECERA_ORIGEN]


def test_marca_valida_da_mcp():
    assert derive_canal("/api/v1/documents/", _marca(), tenant="icetex", authorization=TOKEN) == "mcp"


@pytest.mark.parametrize(
    "caso",
    ["sin_marca", "firma_mala", "otro_token", "otro_tenant", "vencida", "canal_ajeno", "basura"],
)
def test_sin_prueba_valida_el_canal_es_api(caso):
    marca = {
        "sin_marca": None,
        "firma_mala": _marca()[:-4] + "0000",
        "otro_token": _marca(authorization="Bearer otro"),
        "otro_tenant": _marca(tenant="otra"),
        "vencida": _marca(ahora=time.time() - 3600),
        "canal_ajeno": _marca().replace("mcp.", "ui.", 1),
        "basura": "mcp",
    }[caso]
    assert derive_canal("/api/v1/documents/", marca, tenant="icetex", authorization=TOKEN) == "api"


def test_sin_secreto_nunca_se_atribuye_mcp(monkeypatch):
    marca = _marca()
    monkeypatch.delenv("MCP_ORIGIN_SECRET")

    assert derive_canal("/api/v1/documents/", marca, tenant="icetex", authorization=TOKEN) == "api"


async def _canal_que_recibe_el_upstream(test_client, extra: dict[str, str]) -> str:
    client, http_client, _ = test_client
    http_client.get = AsyncMock(return_value=make_validate_response(valid=True))
    wire_upstream(http_client, httpx.Response(status_code=200, json={"ok": True}))
    await client.get(
        "/api/v1/documents/",
        headers={"Authorization": TOKEN, "X-Tenant-Slug": "icetex", **extra},
    )
    return http_client.build_request.call_args.kwargs["headers"]["X-Canal"]


async def test_extremo_a_extremo_la_marca_valida_llega_como_mcp(test_client):
    assert await _canal_que_recibe_el_upstream(test_client, {identidad.CABECERA_ORIGEN: _marca()}) == "mcp"


async def test_extremo_a_extremo_la_marca_falsa_llega_como_api(test_client):
    falsa = _marca()[:-4] + "0000"
    assert await _canal_que_recibe_el_upstream(test_client, {identidad.CABECERA_ORIGEN: falsa}) == "api"


async def test_la_marca_no_se_reenvia_aguas_abajo(test_client):
    await _canal_que_recibe_el_upstream(test_client, {identidad.CABECERA_ORIGEN: _marca()})
    _, http_client, _ = test_client
    reenviadas = {k.lower() for k in http_client.build_request.call_args.kwargs["headers"]}
    assert identidad.CABECERA_ORIGEN.lower() not in reenviadas


async def test_el_gateway_firma_la_ip_real_con_el_secreto_de_origen(test_client):
    """AREA-11-08: el mcp-server verifica la IP SIN poseer el secreto maestro.

    Sin la firma, `ip_real_firmada` devolvía None y todas las llamadas del
    asistente caían de nuevo en el cubo por IP del contenedor del mcp-server.
    """
    await _canal_que_recibe_el_upstream(test_client, {})
    _, http_client, _ = test_client
    enviadas = http_client.build_request.call_args.kwargs["headers"]

    assert identidad.CABECERA_IP_FIRMA in enviadas
    assert identidad.ip_real_firmada(enviadas) == enviadas["X-Real-Ip"]
    # La firma es de OTRO secreto: con uno distinto no verifica.
    import os

    previo = os.environ["MCP_ORIGIN_SECRET"]
    os.environ["MCP_ORIGIN_SECRET"] = "otro-secreto"
    try:
        assert identidad.ip_real_firmada(enviadas) is None
    finally:
        os.environ["MCP_ORIGIN_SECRET"] = previo


async def test_la_idempotency_key_de_la_propuesta_llega_al_servicio(test_client):
    """Sin reenviarla, `payload.correlacion.idempotency_key` moría en el gateway."""
    client, http_client, _ = test_client
    http_client.get = AsyncMock(return_value=make_validate_response(valid=True))
    wire_upstream(http_client, httpx.Response(status_code=200, json={"ok": True}))

    await client.get(
        "/api/v1/documents/",
        headers={"Authorization": TOKEN, "Idempotency-Key": "jti-de-la-propuesta"},
    )

    reenviadas = {k.lower(): v for k, v in http_client.build_request.call_args.kwargs["headers"].items()}
    assert reenviadas.get("idempotency-key") == "jti-de-la-propuesta"


# -- CC-02 (AREA-11-11): la aceptación de la sugerencia del RAG está enrutada -----


def test_aceptar_sugerencia_se_enruta_a_knowledge_y_ingest_sigue_cerrado():
    """Sin la entrada en la tabla el endpoint existe en el servicio y es inalcanzable
    (404 del gateway) — la clase de defecto «pieza correcta que nadie puede llamar»."""
    from types import SimpleNamespace

    from app.routers.proxy import _route_to_upstream

    class _Estado(SimpleNamespace):
        def __getattr__(self, nombre):  # los demás servicios: cualquier otro destino
            return f"http://otro/{nombre}"

    estado = _Estado(knowledge_service_url="http://knowledge:8011")
    sid = "0b1c2d3e-0000-4000-8000-000000000001"

    assert (
        _route_to_upstream(f"/api/v1/knowledge/suggestions/{sid}/aceptar", estado)
        == "http://knowledge:8011"
    )
    assert _route_to_upstream("/api/v1/knowledge/ingest", estado) is None
