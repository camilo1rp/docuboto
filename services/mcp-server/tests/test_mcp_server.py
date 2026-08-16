# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del binding MCP (E18, ADR-019): catálogo MCP + dispatch."""

import httpx
import pytest


def test_build_tools_have_mcp_schema(monkeypatch) -> None:
    from app import mcp_app
    from app.core.config import settings

    monkeypatch.setattr(settings, "mcp_user_permissions", "PERM_RADI")
    tools = mcp_app.build_tools()
    names = {t.name for t in tools}
    assert "radicar_documento" in names and "buscar_radicados" in names
    # Cada tool MCP tiene inputSchema; las de ruta declaran su parámetro requerido.
    by_name = {t.name: t for t in tools}
    assert by_name["consultar_radicado"].inputSchema["required"] == ["document_id"]
    assert by_name["radicar_documento"].inputSchema["type"] == "object"


def test_build_tools_scope_sin_permiso(monkeypatch) -> None:
    from app import mcp_app
    from app.core.config import settings

    monkeypatch.setattr(settings, "mcp_user_permissions", "")
    monkeypatch.setattr(settings, "mcp_user_roles", "")
    names = {t.name for t in mcp_app.build_tools()}
    assert "radicar_documento" not in names  # exige PERM_RADI
    assert "buscar_radicados" in names  # lectura


@pytest.mark.asyncio
async def test_call_dispatches_to_gateway(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    from app import mcp_app
    from app.core.config import settings

    monkeypatch.setattr(settings, "mcp_bearer_token", "tok")
    monkeypatch.setattr(settings, "mcp_tenant_slug", "demo")
    client = AsyncMock()
    client.request.return_value = httpx.Response(200, json={"total": 2})

    text = await mcp_app.call("buscar_radicados", {"q": "tutela"}, client)
    assert '"total": 2' in text or '"total":2' in text
    call = client.request.call_args
    assert call.args[0] == "GET"
    assert call.args[1] == "/api/v1/search"
    assert call.kwargs["headers"]["Authorization"] == "Bearer tok"
    assert call.kwargs["headers"]["X-Tenant-Slug"] == "demo"


@pytest.mark.asyncio
async def test_call_write_without_permission_denied(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    from app import mcp_app
    from app.core.config import settings
    from app.services.dispatcher import DispatchError

    monkeypatch.setattr(settings, "mcp_user_permissions", "")
    monkeypatch.setattr(settings, "mcp_user_roles", "")
    with pytest.raises(DispatchError) as ei:
        await mcp_app.call("radicar_documento", {"doc_type": "E", "subject": "x"}, AsyncMock())
    assert ei.value.code == "permiso_insuficiente"


@pytest.mark.asyncio
async def test_call_unknown_tool(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    from app import mcp_app
    from app.services.dispatcher import DispatchError

    with pytest.raises(DispatchError) as ei:
        await mcp_app.call("inexistente", {}, AsyncMock())
    assert ei.value.code == "tool_not_found"


def test_set_context_overrides_env(monkeypatch) -> None:
    """El contexto por cabeceras (HTTP) prevalece sobre el entorno (stdio)."""
    from app import mcp_app
    from app.core.config import settings

    monkeypatch.setattr(settings, "mcp_user_permissions", "")  # entorno: sin permisos
    tok = mcp_app.set_context(
        authorization="Bearer h", tenant="t1", permissions={"PERM_RADI"}, is_root=False
    )
    try:
        names = {t.name for t in mcp_app.build_tools()}
        assert "radicar_documento" in names  # gracias al contexto HTTP
        assert mcp_app._session()["authorization"] == "Bearer h"
        assert mcp_app._session()["tenant"] == "t1"
    finally:
        mcp_app.reset_context(tok)


@pytest.mark.asyncio
async def test_streamable_http_initialize() -> None:
    """El transporte HTTP streamable /mcp responde al handshake MCP."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app, lifespan

    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "1"},
        },
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/mcp/", json=body, headers=headers)
    assert resp.status_code == 200
    assert "protocolVersion" in resp.text and "orpycamcp" in resp.text.lower()


@pytest.mark.asyncio
async def test_el_binding_no_ejecuta_escrituras_y_devuelve_propuesta(monkeypatch) -> None:
    """El transporte que ANTES auto-confirmaba.

    `mcp_app.call` llamaba a `authorize(..., confirm_write=True)`: daba la
    escritura por aprobada «porque el human-in-the-loop lo aporta el cliente
    MCP». Eso convertía una garantía en una suposición sobre software ajeno — un
    cliente que no preguntara nada ejecutaba igual.

    Este test faltaba, y su ausencia se vio por mutación: quitar el gate del
    binding dejaba la suite entera en verde.
    """
    import json as _json
    from unittest.mock import AsyncMock

    from app import mcp_app
    from app.core.config import settings

    monkeypatch.setattr(settings, "mcp_user_permissions", "PERM_RADI")
    monkeypatch.setattr(settings, "mcp_bearer_token", "tok")
    monkeypatch.setattr(settings, "mcp_tenant_slug", "demo")
    monkeypatch.setattr(settings, "mcp_proposal_secret", "clave-de-pruebas")

    client = AsyncMock()
    texto = await mcp_app.call(
        "radicar_documento", {"doc_type": "E", "subject": "Tutela"}, client
    )

    cuerpo = _json.loads(texto)
    assert cuerpo["kind"] == "proposal"
    assert cuerpo["ejecutado"] is False
    assert cuerpo["proposal"]["token"].startswith("op1.")
    # Lo esencial: no se despachó nada al gateway.
    client.request.assert_not_called()
    client.post.assert_not_called()
