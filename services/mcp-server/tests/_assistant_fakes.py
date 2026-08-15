# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Dobles de prueba del asistente (E18): API de Anthropic + gateway simulados.

NUNCA se toca la red: `FakeAnthropic` devuelve respuestas prefabricadas y el
gateway simulado enruta `/api/v1/auth/me` (identidad) y las llamadas de tool a
respuestas httpx en memoria. Los bloques imitan la forma real del SDK (atributos
`.type/.text/.id/.name/.input`) que consume `run_turn`/`_execute_tool`.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx


class Block:
    """Bloque de contenido estilo SDK Anthropic (text | tool_use)."""

    def __init__(
        self,
        type_: str,
        *,
        text: str | None = None,
        id: str | None = None,
        name: str | None = None,
        input: dict[str, Any] | None = None,
    ) -> None:
        self.type = type_
        self.text = text
        self.id = id
        self.name = name
        self.input = input


def text_block(text: str) -> Block:
    return Block("text", text=text)


def tool_use_block(id: str, name: str, input: dict[str, Any]) -> Block:
    return Block("tool_use", id=id, name=name, input=input)


class Response:
    """Respuesta estilo `messages.create` (content + stop_reason)."""

    def __init__(self, content: list[Block], stop_reason: str) -> None:
        self.content = content
        self.stop_reason = stop_reason


class FakeMessages:
    def __init__(self, responses: list[Response]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Response:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeAnthropic quedó sin respuestas prefabricadas")
        return self._responses.pop(0)


class FakeAnthropic:
    """Sustituto de `anthropic.AsyncAnthropic` para los tests del asistente."""

    def __init__(self, responses: list[Response]) -> None:
        self.messages = FakeMessages(responses)


class RaisingAnthropic:
    """Cliente cuyo `messages.create` lanza -> ejercita la degradación honesta."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

        async def _create(**kwargs: Any) -> Any:
            raise self._exc

        self.messages = type("M", (), {"create": staticmethod(_create)})()


def make_gateway(
    *,
    me_status: int = 200,
    permissions: list[str] | None = None,
    roles: list[str] | None = None,
    user_id: str = "user-1",
    tool_response: httpx.Response | None = None,
) -> AsyncMock:
    """Gateway simulado, con los dos caminos separados a propósito.

    - `.get` sirve las **lecturas de contexto**: `/auth/me` (identidad),
      `/auth/clearance` y `/auth/context` (marco de alcance).
    - `.request` sirve los **despachos de tool**, que es por donde pasa ahora el
      dispatcher para poder usar PATCH/PUT/DELETE.

    Mantenerlos separados no es un detalle del doble: es lo que permite que
    `gateway.request.await_count == 0` signifique «no se despachó ninguna tool»
    sin que la aserción se vacíe cuando una tool cambia de método. Con el mock
    anterior, atado a `.post`, un despacho por GET habría pasado inadvertido.

    `tool_response` es la respuesta que se devuelve a cualquier despacho.
    `user_id` es la identidad validada que `/auth/me` devuelve (llavea el
    historial).
    """
    gw = AsyncMock()
    me_body = {
        "permissions": permissions or [],
        "permission_levels": {p: 3 for p in (permissions or [])},
        "roles": roles or [],
        "user_id": user_id,
    }

    async def _get(path: str, **kwargs: Any) -> httpx.Response:
        if path == "/api/v1/auth/me":
            if me_status == 200:
                return httpx.Response(200, json=me_body)
            return httpx.Response(me_status, json={"error": "x"})
        if path == "/api/v1/auth/clearance":
            return httpx.Response(
                200, json={"user_id": user_id, "max_level": 3, "accessible_levels": [1, 2, 3]}
            )
        if path == "/api/v1/auth/context":
            return httpx.Response(
                200, json={"user_id": user_id, "active_depe_id": None, "available_depts": []}
            )
        return tool_response if tool_response is not None else httpx.Response(200, json={})

    gw.get.side_effect = _get

    async def _request(method: str, path: str, **kwargs: Any) -> httpx.Response:
        return tool_response if tool_response is not None else httpx.Response(200, json={})

    gw.request.side_effect = _request
    return gw
