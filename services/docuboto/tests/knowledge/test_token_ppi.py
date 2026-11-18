# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El token de servicio se pide al realm y no se envía una petición sin él."""

from __future__ import annotations

import base64
from typing import Any

import httpx
import pytest
from orpycamcp_common.kek import seal

from knowledge.core import token_ppi
from knowledge.core.config import settings

KEK = b"\x66" * 32
SECRETO = b"secreto-de-servicio-demo"


class _Conn:
    def __init__(self, fila: dict[str, Any] | None) -> None:
        self.fila = fila
        self.lecturas = 0

    async def fetchrow(self, *_a: object, **_k: object) -> dict[str, Any] | None:
        self.lecturas += 1
        return self.fila


def _fila() -> dict[str, Any]:
    ciphertext, nonce = seal(
        SECRETO, key=KEK, aad=token_ppi.aad("demo")
    )
    return {
        "client_id": "intelligence-demo",
        "ciphertext": ciphertext,
        "nonce": nonce,
        "kek_version": 1,
    }


@pytest.fixture(autouse=True)
def _entorno(monkeypatch: pytest.MonkeyPatch) -> None:
    token_ppi.olvidar()
    monkeypatch.setattr(
        settings, "intelligence_credential_kek", base64.b64encode(KEK).decode()
    )
    monkeypatch.setattr(settings, "keycloak_url", "http://keycloak:8080")
    monkeypatch.setattr(settings, "keycloak_realm", "orpycamcp")


def _cliente(handler: Any) -> Any:
    original = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    return factory


@pytest.mark.asyncio
async def test_pide_client_credentials_y_guarda_el_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pedidos: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        pedidos.append(req)
        return httpx.Response(200, json={"access_token": "abc.def.ghi", "expires_in": 300})

    monkeypatch.setattr(httpx, "AsyncClient", _cliente(handler))
    conn = _Conn(_fila())
    primero = await token_ppi.portador(conn, "demo")
    segundo = await token_ppi.portador(conn, "demo")
    assert primero == segundo == "abc.def.ghi"
    assert len(pedidos) == 1
    assert conn.lecturas == 1
    assert pedidos[0].url.path.endswith("/protocol/openid-connect/token")
    cuerpo = dict(httpx.QueryParams(pedidos[0].content.decode()))
    assert cuerpo["grant_type"] == "client_credentials"
    assert cuerpo["client_id"] == "intelligence-demo"
    assert cuerpo["client_secret"] == SECRETO.decode()


@pytest.mark.asyncio
async def test_sin_secreto_no_hay_token(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("no debía llamar al realm")

    monkeypatch.setattr(httpx, "AsyncClient", _cliente(handler))
    with pytest.raises(RuntimeError, match="sin cliente"):
        await token_ppi.portador(_Conn(None), "demo")


@pytest.mark.asyncio
async def test_el_realm_caido_no_inventa_un_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="no")

    monkeypatch.setattr(httpx, "AsyncClient", _cliente(handler))
    with pytest.raises(RuntimeError, match="sin token"):
        await token_ppi.portador(_Conn(_fila()), "demo")
