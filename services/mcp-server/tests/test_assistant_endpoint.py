# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests del LÍMITE HTTP del asistente (E18/ADR-019): `POST /api/v1/assistant/message`.

Verifican la degradación honesta (503 sin clave), los guardas de identidad
(401/400), la validación de esquema (422) y el mapeo de errores del servicio a
códigos HTTP. La orquestación en sí se prueba en `test_assistant_service`; aquí
el servicio se controla vía el estado de la app (Anthropic/gateway simulados).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests._assistant_fakes import FakeAnthropic, RaisingAnthropic, Response, make_gateway, text_block

_AUTH = {"Authorization": "Bearer tok", "X-Tenant-Slug": "demo"}


@pytest_asyncio.fixture
async def api() -> AsyncGenerator[AsyncClient, None]:
    """Cliente HTTP con el estado del asistente reseteado por test."""
    # ASGITransport NO corre el lifespan; se fija el estado a mano (como conftest).
    app.state.gateway_client = make_gateway(permissions=[])
    app.state.anthropic_client = FakeAnthropic([Response([text_block("Hola.")], "end_turn")])
    app.state.assistant_available = True
    app.state.conversations = {}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# -- degradación honesta ----------------------------------------------


@pytest.mark.asyncio
async def test_503_cuando_asistente_no_disponible(api: AsyncClient) -> None:
    app.state.assistant_available = False  # sin ANTHROPIC_API_KEY en el arranque real

    resp = await api.post("/api/v1/assistant/message", json={"mensaje": "hola"}, headers=_AUTH)

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "assistant_unavailable"


@pytest.mark.asyncio
async def test_503_cuando_el_sdk_falla(api: AsyncClient) -> None:
    # Cualquier fallo del SDK (auth, red, rate limit) -> 503, nunca 500 crudo.
    app.state.anthropic_client = RaisingAnthropic(RuntimeError("boom del SDK"))

    resp = await api.post("/api/v1/assistant/message", json={"mensaje": "hola"}, headers=_AUTH)

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "assistant_unavailable"


# -- guardas de identidad ---------------------------------------------


@pytest.mark.asyncio
async def test_401_sin_authorization(api: AsyncClient) -> None:
    resp = await api.post(
        "/api/v1/assistant/message", json={"mensaje": "hola"}, headers={"X-Tenant-Slug": "demo"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "unauthenticated"


@pytest.mark.asyncio
async def test_400_sin_tenant(api: AsyncClient) -> None:
    resp = await api.post(
        "/api/v1/assistant/message", json={"mensaje": "hola"}, headers={"Authorization": "Bearer t"}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "missing_tenant"


# -- validación de esquema --------------------------------------------


@pytest.mark.asyncio
async def test_422_mensaje_vacio(api: AsyncClient) -> None:
    resp = await api.post("/api/v1/assistant/message", json={"mensaje": ""}, headers=_AUTH)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_422_mensaje_demasiado_largo(api: AsyncClient) -> None:
    resp = await api.post(
        "/api/v1/assistant/message", json={"mensaje": "x" * 4001}, headers=_AUTH
    )
    assert resp.status_code == 422


# -- camino feliz y mapeo de errores del servicio ---------------------


@pytest.mark.asyncio
async def test_200_devuelve_turno_del_asistente(api: AsyncClient) -> None:
    resp = await api.post("/api/v1/assistant/message", json={"mensaje": "hola"}, headers=_AUTH)

    assert resp.status_code == 200
    body = resp.json()
    assert body["message"]["texto"] == "Hola."
    assert body["message"]["role"] == "assistant"
    assert body["conversation_id"]


@pytest.mark.asyncio
async def test_identity_401_se_propaga_como_401(api: AsyncClient) -> None:
    # /auth/me responde 401 -> IdentityError(401) -> HTTP 401 (no 503).
    app.state.gateway_client = make_gateway(me_status=401)

    resp = await api.post("/api/v1/assistant/message", json={"mensaje": "hola"}, headers=_AUTH)

    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "unauthenticated"


@pytest.mark.asyncio
async def test_conversation_id_se_reusa_en_la_respuesta(api: AsyncClient) -> None:
    resp = await api.post(
        "/api/v1/assistant/message",
        json={"mensaje": "hola", "conversation_id": "conv-xyz"},
        headers=_AUTH,
    )
    assert resp.status_code == 200
    assert resp.json()["conversation_id"] == "conv-xyz"
