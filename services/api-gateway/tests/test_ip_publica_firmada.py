# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""B2 (ADR-031): la IP de una ruta pública sale de saltos de confianza, no del XFF crudo."""

import httpx

from app.main import app

from .conftest import wire_upstream

RUTA = "/api/v1/public/icetex/ventanilla/radicar"


def _enviado(http_client) -> dict[str, str]:
    return {k.lower(): v for k, v in http_client.build_request.call_args.kwargs["headers"].items()}


async def test_la_ip_publica_es_la_del_socket_sin_proxies(test_client):
    client, http_client, _ = test_client
    wire_upstream(http_client, httpx.Response(200, json={"ok": True}))
    app.state.trusted_proxies = ""

    r = await client.post(RUTA, json={})

    assert r.status_code == 200
    assert _enviado(http_client)["x-real-ip"] == "127.0.0.1"
    assert "x-public-ip-signature" not in _enviado(http_client)


async def test_xff_forjado_de_cliente_directo_se_ignora(test_client):
    client, http_client, _ = test_client
    wire_upstream(http_client, httpx.Response(200, json={"ok": True}))
    app.state.trusted_proxies = ""

    await client.post(RUTA, json={}, headers={"X-Forwarded-For": "6.6.6.6"})

    assert _enviado(http_client)["x-real-ip"] == "127.0.0.1"


async def test_x_real_ip_del_cliente_se_sobrescribe(test_client):
    client, http_client, _ = test_client
    wire_upstream(http_client, httpx.Response(200, json={"ok": True}))
    app.state.trusted_proxies = ""

    await client.post(
        RUTA,
        json={},
        headers={"X-Real-Ip": "6.6.6.6"},
    )

    assert _enviado(http_client)["x-real-ip"] == "127.0.0.1"


async def test_con_proxy_de_confianza_toma_el_cliente_del_xff(test_client):
    client, http_client, _ = test_client
    wire_upstream(http_client, httpx.Response(200, json={"ok": True}))
    app.state.trusted_proxies = "127.0.0.0/8"
    try:
        await client.post(RUTA, json={}, headers={"X-Forwarded-For": "203.0.113.7"})
    finally:
        app.state.trusted_proxies = ""

    assert _enviado(http_client)["x-real-ip"] == "203.0.113.7"
