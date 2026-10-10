# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""B2 (ADR-031): la IP de una ruta pública viaja firmada y no sale del XFF crudo."""

import httpx
import pytest
from orpycamcp_common import identidad

from app.main import app

from .conftest import wire_upstream

SECRETO = "secreto-identidad-de-prueba"
RUTA = "/api/v1/public/icetex/ventanilla/radicar"


@pytest.fixture(autouse=True)
def _secreto(monkeypatch):
    monkeypatch.setenv("IDENTITY_SIGNING_SECRET", SECRETO)


def _enviado(http_client) -> dict[str, str]:
    return {k.lower(): v for k, v in http_client.build_request.call_args.kwargs["headers"].items()}


async def test_la_ip_publica_llega_firmada_y_ligada_a_la_ruta(test_client):
    client, http_client, _ = test_client
    wire_upstream(http_client, httpx.Response(200, json={"ok": True}))
    app.state.trusted_proxies = ""

    r = await client.post(RUTA, json={})

    assert r.status_code == 200
    h = _enviado(http_client)
    # Cliente directo (sin proxies de confianza): la IP es la del socket.
    assert identidad.ip_publica_verificada(h, RUTA) == "127.0.0.1"
    assert identidad.ip_publica_verificada(h, RUTA + "/otra") is None


async def test_xff_forjado_de_cliente_directo_se_ignora(test_client):
    client, http_client, _ = test_client
    wire_upstream(http_client, httpx.Response(200, json={"ok": True}))
    app.state.trusted_proxies = ""

    await client.post(RUTA, json={}, headers={"X-Forwarded-For": "6.6.6.6"})

    assert identidad.ip_publica_verificada(_enviado(http_client), RUTA) == "127.0.0.1"


async def test_x_real_ip_del_cliente_se_sobrescribe(test_client):
    client, http_client, _ = test_client
    wire_upstream(http_client, httpx.Response(200, json={"ok": True}))
    app.state.trusted_proxies = ""

    await client.post(
        RUTA,
        json={},
        headers={
            "X-Real-Ip": "6.6.6.6",
            "X-Real-Ip-Ts": "1",
            "X-Public-Ip-Signature": "deadbeef",
        },
    )

    h = _enviado(http_client)
    assert h["x-real-ip"] == "127.0.0.1"
    assert h["x-real-ip-ts"] != "1"
    assert identidad.ip_publica_verificada(h, RUTA) == "127.0.0.1"


async def test_con_proxy_de_confianza_toma_el_cliente_del_xff(test_client):
    client, http_client, _ = test_client
    wire_upstream(http_client, httpx.Response(200, json={"ok": True}))
    app.state.trusted_proxies = "127.0.0.0/8"
    try:
        await client.post(RUTA, json={}, headers={"X-Forwarded-For": "203.0.113.7"})
    finally:
        app.state.trusted_proxies = ""

    assert identidad.ip_publica_verificada(_enviado(http_client), RUTA) == "203.0.113.7"


async def test_sin_secreto_no_se_inyecta_nada(test_client, monkeypatch):
    client, http_client, _ = test_client
    wire_upstream(http_client, httpx.Response(200, json={"ok": True}))
    monkeypatch.delenv("IDENTITY_SIGNING_SECRET")

    await client.post(RUTA, json={}, headers={"X-Real-Ip": "6.6.6.6"})

    h = _enviado(http_client)
    assert "x-public-ip-signature" not in h
    assert identidad.ip_publica_verificada(h, RUTA) is None
