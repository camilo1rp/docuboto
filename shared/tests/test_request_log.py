# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Registro estructurado por petición en los servicios (11.2)."""

from __future__ import annotations

import json
import logging

import pytest
from orpycamcp_common.request_log import RequestLogMiddleware, request_id_actual


async def _app_ok(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"{}"})


def _scope(path="/api/v1/documents/", cabeceras=None):
    return {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": b"q=2024-ICETEX-E-000001",
        "headers": cabeceras or [],
    }


async def _ejecutar(app, scope):
    enviados = []

    async def send(message):
        enviados.append(message)

    async def receive():
        return {"type": "http.request", "body": b""}

    await app(scope, receive, send)
    return enviados


def _linea(caplog):
    registros = [r for r in caplog.records if r.name.endswith(".access")]
    assert registros, "el servicio no registró la petición"
    return json.loads(registros[-1].message)


@pytest.mark.asyncio
async def test_registra_identificador_tenant_y_usuario(caplog):
    app = RequestLogMiddleware(_app_ok, servicio="document-service")
    scope = _scope(
        cabeceras=[
            (b"x-request-id", b"abc-123"),
            (b"x-tenant-slug", b"icetex"),
            (b"x-user-id", b"11111111-1111-1111-1111-111111111111"),
        ]
    )

    with caplog.at_level(logging.INFO):
        await _ejecutar(app, scope)

    registro = _linea(caplog)
    assert registro["servicio"] == "document-service"
    assert registro["request_id"] == "abc-123"
    assert registro["tenant"] == "icetex"
    assert registro["usuario"] == "11111111-1111-1111-1111-111111111111"
    assert registro["status"] == 200
    assert registro["path"] == "/api/v1/documents/"


@pytest.mark.asyncio
async def test_no_registra_la_cadena_de_consulta(caplog):
    """Lleva números de radicado y términos de búsqueda: contenido documental
    clasificado que no debe quedar en los registros del contenedor."""
    app = RequestLogMiddleware(_app_ok, servicio="document-service")

    with caplog.at_level(logging.INFO):
        await _ejecutar(app, _scope())

    assert "2024-ICETEX-E-000001" not in json.dumps(_linea(caplog))


@pytest.mark.asyncio
async def test_una_peticion_que_revienta_tambien_se_registra(caplog):
    """Es justo la petición que hay que poder encontrar después."""

    async def _app_rota(scope, receive, send):
        raise RuntimeError("boom")

    app = RequestLogMiddleware(_app_rota, servicio="archive-service")

    with caplog.at_level(logging.INFO), pytest.raises(RuntimeError):
        await _ejecutar(app, _scope(cabeceras=[(b"x-request-id", b"traza-1")]))

    registro = _linea(caplog)
    assert registro["request_id"] == "traza-1"
    assert registro["status"] == 500


@pytest.mark.asyncio
async def test_el_identificador_esta_disponible_durante_la_peticion_y_no_despues():
    """El `ContextVar` evita pasar el identificador por veinte firmas; que se
    limpie al salir evita que una petición herede la traza de la anterior."""
    visto = {}

    async def _app_que_mira(scope, receive, send):
        visto["dentro"] = request_id_actual()
        await _app_ok(scope, receive, send)

    app = RequestLogMiddleware(_app_que_mira, servicio="workflow-service")
    await _ejecutar(app, _scope(cabeceras=[(b"x-request-id", b"ctx-9")]))

    assert visto["dentro"] == "ctx-9"
    assert request_id_actual() == ""


@pytest.mark.asyncio
async def test_sin_cabecera_los_campos_van_a_nulo_no_a_cadena_vacia(caplog):
    """Un `""` en el registro se confunde con "el valor era vacío"; `null` dice
    lo que pasó: la petición no traía identidad."""
    app = RequestLogMiddleware(_app_ok, servicio="tenant-service")

    with caplog.at_level(logging.INFO):
        await _ejecutar(app, _scope())

    registro = _linea(caplog)
    assert registro["request_id"] is None
    assert registro["tenant"] is None
    assert registro["usuario"] is None


@pytest.mark.asyncio
async def test_ignora_trafico_que_no_es_http():
    """websocket/lifespan pasan de largo sin tocar nada."""
    llamado = {}

    async def _app(scope, receive, send):
        llamado["si"] = True

    app = RequestLogMiddleware(_app, servicio="knowledge-service")
    await app({"type": "lifespan"}, None, None)
    assert llamado["si"]
