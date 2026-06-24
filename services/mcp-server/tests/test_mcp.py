# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de la capa MCP: catálogo, scope, dispatch (E18, ADR-019)."""

import httpx
import pytest

from app.tools.catalog import get_tool, visible_tools

# -- catálogo / scope (unidad) ----------------------------------------


def test_catalog_has_core_tools() -> None:
    assert get_tool("radicar_documento") is not None
    assert get_tool("buscar_radicados").method == "GET"
    assert get_tool("radicar_documento").write is True


def test_visible_tools_filters_by_permission() -> None:
    # radicar_documento exige RADI_CREAR; sin él no es visible.
    sin = {t.name for t in visible_tools(set(), is_root=False)}
    assert "radicar_documento" not in sin
    assert "buscar_radicados" in sin  # lectura, sin permiso → visible

    con = {t.name for t in visible_tools({"RADI_CREAR"}, is_root=False)}
    assert "radicar_documento" in con

    root = {t.name for t in visible_tools(set(), is_root=True)}
    assert "radicar_documento" in root  # ROOT ve todas


# -- endpoints --------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tools_endpoint(client_with_gateway) -> None:
    client, _ = client_with_gateway
    resp = await client.get("/api/v1/mcp/tools", headers={"X-User-Permissions": "RADI_CREAR"})
    assert resp.status_code == 200
    names = {t["name"] for t in resp.json()["tools"]}
    assert "radicar_documento" in names and "listar_trd" in names


@pytest.mark.asyncio
async def test_invoke_read_tool_dispatches_to_gateway(client_with_gateway) -> None:
    client, gateway = client_with_gateway
    gateway.get.return_value = httpx.Response(200, json={"total": 1, "hits": []})

    resp = await client.post(
        "/api/v1/mcp/tools/buscar_radicados/invoke",
        json={"q": "tutela"},
        headers={"Authorization": "Bearer tok", "X-Tenant-Slug": "demo"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == 200
    assert body["data"]["total"] == 1
    # Reenvió a la ruta del gateway con Authorization + tenant.
    call = gateway.get.call_args
    assert call.args[0] == "/api/v1/search"
    assert call.kwargs["headers"]["Authorization"] == "Bearer tok"
    assert call.kwargs["headers"]["X-Tenant-Slug"] == "demo"


@pytest.mark.asyncio
async def test_invoke_write_requires_confirmation(client_with_gateway) -> None:
    client, gateway = client_with_gateway
    resp = await client.post(
        "/api/v1/mcp/tools/radicar_documento/invoke",
        json={"doc_type": "E", "subject": "x"},
        headers={"X-User-Permissions": "RADI_CREAR"},  # sin X-Confirm-Write
    )
    assert resp.status_code == 412
    assert resp.json()["detail"]["error"] == "confirmacion_requerida"
    gateway.post.assert_not_called()


@pytest.mark.asyncio
async def test_invoke_write_without_permission_forbidden(client_with_gateway) -> None:
    client, _ = client_with_gateway
    resp = await client.post(
        "/api/v1/mcp/tools/radicar_documento/invoke",
        json={"doc_type": "E", "subject": "x"},
        headers={"X-Confirm-Write": "true"},  # sin RADI_CREAR
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "permiso_insuficiente"


@pytest.mark.asyncio
async def test_invoke_write_path_param_and_confirm(client_with_gateway) -> None:
    client, gateway = client_with_gateway
    gateway.post.return_value = httpx.Response(201, json={"tracking_number": "2024-DEMO-E-000001"})

    resp = await client.post(
        "/api/v1/mcp/tools/radicar_documento/invoke",
        json={"doc_type": "E", "subject": "Tutela"},
        headers={
            "X-User-Permissions": "RADI_CREAR",
            "X-Confirm-Write": "true",
            "Authorization": "Bearer tok",
            "X-Tenant-Slug": "demo",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["tracking_number"] == "2024-DEMO-E-000001"
    assert gateway.post.call_args.args[0] == "/api/v1/documents"


@pytest.mark.asyncio
async def test_invoke_unknown_tool(client_with_gateway) -> None:
    client, _ = client_with_gateway
    resp = await client.post("/api/v1/mcp/tools/inexistente/invoke", json={})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_invoke_path_param_fills_route(client_with_gateway) -> None:
    client, gateway = client_with_gateway
    gateway.get.return_value = httpx.Response(200, json={"version": 0})
    eid = "11111111-1111-1111-1111-111111111111"

    resp = await client.post(
        "/api/v1/mcp/tools/indice_expediente/invoke",
        json={"expediente_id": eid},
        headers={"Authorization": "Bearer t", "X-Tenant-Slug": "demo"},
    )
    assert resp.status_code == 200
    assert gateway.get.call_args.args[0] == f"/api/v1/expedientes/{eid}/indice"
