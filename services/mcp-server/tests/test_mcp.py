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
    # radicar_documento exige PERM_RADI; sin él no es visible.
    sin = {t.name for t in visible_tools(set(), is_root=False)}
    assert "radicar_documento" not in sin
    assert "buscar_radicados" in sin  # lectura, sin permiso → visible

    con = {t.name for t in visible_tools({"PERM_RADI"}, is_root=False)}
    assert "radicar_documento" in con

    root = {t.name for t in visible_tools(set(), is_root=True)}
    assert "radicar_documento" in root  # ROOT ve todas


# -- endpoints --------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tools_endpoint(client_with_gateway) -> None:
    """El catálogo del llamante sale de `/auth/me`, no de una cabecera.

    Se llama **sin** `X-User-Permissions` a propósito: era la cabecera que este
    endpoint leía, y no la produce nadie —el gateway la descarta por forjable—,
    de modo que el endpoint que existe para decirle a alguien qué puede hacer le
    contestaba a todo el mundo con las tools sin permiso. Que la respuesta sea
    correcta sin mandarla es justo lo que hay que fijar; que lo siguiera siendo
    mandándola no probaría nada.
    """
    client, _ = client_with_gateway
    resp = await client.get("/api/v1/mcp/tools")
    assert resp.status_code == 200
    names = {t["name"] for t in resp.json()["tools"]}
    assert "radicar_documento" in names and "listar_trd" in names


@pytest.mark.asyncio
async def test_el_permiso_forjado_sin_su_nivel_no_alcanza_ninguna_tool(client_with_gateway) -> None:
    """Enviarla no añade ni quita — y este test NO prueba que no se lea.

    Prueba la defensa compuesta: aunque alguien volviera a leer la cabecera, el
    permiso forjado llega **sin su nivel**, y `visible_tools` exige también
    nivel (`levels.get(p, 0)`), que solo sale de `/auth/me`. Eso es defensa en
    profundidad y merece un test.

    Lo que NO prueba es lo que su nombre sugiere. Se vio por mutación: volver a
    leer la cabecera dejaba esta comprobación en verde. Que no se lea lo fija
    `test_la_cabecera_fantasma_no_se_lee_en_ninguna_parte_del_codigo`, que es
    una afirmación sobre la declaración y no sobre el resultado.
    """
    client, _ = client_with_gateway
    limpio = await client.get("/api/v1/mcp/tools")
    forjado = await client.get(
        "/api/v1/mcp/tools",
        headers={"X-User-Permissions": "USUA_PERM_TRD,PERM_TRAMITAR", "X-User-Roles": "ROOT"},
    )
    assert forjado.status_code == 200
    assert {t["name"] for t in forjado.json()["tools"]} == {
        t["name"] for t in limpio.json()["tools"]
    }


@pytest.mark.asyncio
async def test_invoke_read_tool_dispatches_to_gateway(client_with_gateway) -> None:
    client, gateway = client_with_gateway
    gateway.request.return_value = httpx.Response(200, json={"total": 1, "hits": []})

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
    call = gateway.request.call_args
    assert call.args[0] == "GET"
    assert call.args[1] == "/api/v1/search"
    assert call.kwargs["headers"]["Authorization"] == "Bearer tok"
    assert call.kwargs["headers"]["X-Tenant-Slug"] == "demo"


@pytest.mark.asyncio
async def test_invocar_una_escritura_devuelve_propuesta_y_no_ejecuta(
    client_with_gateway,
) -> None:
    """El invariante del gate: una escritura NO se ejecuta por esta vía.

    Antes se exigía `X-Confirm-Write: true` y se respondía 412 si faltaba. El
    problema no era el 412: era que la cabecera la ponía cualquiera y no ataba la
    confirmación a ningún acto concreto — confirmaba «algo», no «esto».

    Ahora se devuelve una PROPUESTA en lugar de un error. Es deliberado: un
    cliente antiguo u hostil que fabrique cualquier cabecera obtiene una
    descripción del acto, no un efecto. El camino seguro es el que sale por
    defecto, no el que hay que acordarse de proteger.
    """
    client, gateway = client_with_gateway
    resp = await client.post(
        "/api/v1/mcp/tools/radicar_documento/invoke",
        json={"doc_type": "E", "subject": "Tutela"},
        headers={"Authorization": "Bearer tok", "X-Tenant-Slug": "demo"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "proposal"
    assert body["ejecutado"] is False
    assert body["proposal"]["token"].startswith("op1.")
    assert "NO SE HA EJECUTADO NADA" in body["aviso"]
    # Lo que de verdad importa: el gateway no recibió ninguna escritura.
    gateway.request.assert_not_called()


@pytest.mark.asyncio
async def test_la_cabecera_de_confirmacion_ya_no_ejecuta_nada(client_with_gateway) -> None:
    """Enviar `X-Confirm-Write: true` no cambia el resultado.

    Es el test que impide una regresión cómoda: mientras esa cabecera siguiera
    surtiendo efecto en alguna rama, el gate nuevo sería opcional.
    """
    client, gateway = client_with_gateway
    resp = await client.post(
        "/api/v1/mcp/tools/radicar_documento/invoke",
        json={"doc_type": "E", "subject": "Tutela"},
        headers={
            "Authorization": "Bearer tok",
            "X-Tenant-Slug": "demo",
            "X-Confirm-Write": "true",
        },
    )
    assert resp.json()["kind"] == "proposal"
    gateway.request.assert_not_called()


@pytest.mark.asyncio
async def test_sin_permiso_no_hay_ni_propuesta(client_with_gateway) -> None:
    """El scope se comprueba ANTES de proponer: no se describe lo que no se puede.

    Emitir una propuesta a quien no puede ejecutarla sería enseñarle una puerta
    que se le va a cerrar en la cara — y, de paso, un oráculo sobre qué
    operaciones existen.
    """
    client, gateway = client_with_gateway

    async def _sin_permisos(path: str, **kwargs: object):
        import httpx as _h

        if path == "/api/v1/auth/me":
            return _h.Response(
                200,
                json={
                    "user_id": "11111111-1111-1111-1111-111111111111",
                    "tenant_slug": "demo",
                    "roles": [],
                    "permissions": [],
                    "permission_levels": {},
                },
            )
        return _h.Response(200, json={})

    gateway.get.side_effect = _sin_permisos

    resp = await client.post(
        "/api/v1/mcp/tools/radicar_documento/invoke",
        json={"doc_type": "E", "subject": "x"},
        headers={"Authorization": "Bearer tok", "X-Tenant-Slug": "demo"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "permiso_insuficiente"
    gateway.request.assert_not_called()


@pytest.mark.asyncio
async def test_invoke_unknown_tool(client_with_gateway) -> None:
    client, _ = client_with_gateway
    resp = await client.post("/api/v1/mcp/tools/inexistente/invoke", json={})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_invoke_path_param_fills_route(client_with_gateway) -> None:
    client, gateway = client_with_gateway
    gateway.request.return_value = httpx.Response(200, json={"version": 0})
    eid = "11111111-1111-1111-1111-111111111111"

    resp = await client.post(
        "/api/v1/mcp/tools/indice_expediente/invoke",
        json={"expediente_id": eid},
        headers={"Authorization": "Bearer t", "X-Tenant-Slug": "demo"},
    )
    assert resp.status_code == 200
    assert gateway.request.call_args.args[1] == f"/api/v1/expedientes/{eid}/indice"


# ---------------------------------------------------------------------------
# La segunda mitad del gate: ejecutar la propuesta
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_el_ciclo_completo_proponer_y_confirmar(client_with_gateway) -> None:
    """Nada se escribe hasta confirmar; al confirmar, se escribe lo propuesto."""
    client, gateway = client_with_gateway
    gateway.request.return_value = httpx.Response(
        201, json={"tracking_number": "2026-DEMO-E-000001"}
    )
    cabeceras = {"Authorization": "Bearer tok", "X-Tenant-Slug": "demo"}

    propuesta = await client.post(
        "/api/v1/mcp/tools/radicar_documento/invoke",
        json={"doc_type": "E", "subject": "Tutela"},
        headers=cabeceras,
    )
    token = propuesta.json()["proposal"]["token"]
    gateway.request.assert_not_called()  # proponer no escribe

    hecho = await client.post(
        "/api/v1/mcp/proposals/execute", json={"token": token}, headers=cabeceras
    )

    assert hecho.status_code == 200
    cuerpo = hecho.json()
    assert cuerpo["ejecutado"] is True
    assert cuerpo["data"]["tracking_number"] == "2026-DEMO-E-000001"
    # Y lo ejecutado es EXACTAMENTE lo propuesto.
    metodo, ruta = gateway.request.call_args.args[:2]
    assert (metodo, ruta) == ("POST", "/api/v1/documents")
    assert gateway.request.call_args.kwargs["json"] == {"doc_type": "E", "subject": "Tutela"}


@pytest.mark.asyncio
async def test_los_argumentos_del_cuerpo_no_alteran_el_acto(client_with_gateway) -> None:
    """El cuerpo de la confirmación lleva SOLO el token.

    Si la ejecución admitiera argumentos, se aprobaría una cosa y se ejecutaría
    otra: la firma dejaría de significar nada y volveríamos al booleano que todo
    esto vino a sustituir.
    """
    client, gateway = client_with_gateway
    gateway.request.return_value = httpx.Response(201, json={})
    cabeceras = {"Authorization": "Bearer tok", "X-Tenant-Slug": "demo"}

    propuesta = await client.post(
        "/api/v1/mcp/tools/radicar_documento/invoke",
        json={"doc_type": "E", "subject": "Lo aprobado"},
        headers=cabeceras,
    )
    token = propuesta.json()["proposal"]["token"]

    await client.post(
        "/api/v1/mcp/proposals/execute",
        json={"token": token, "subject": "LO QUE NADIE APROBÓ", "doc_type": "S"},
        headers=cabeceras,
    )

    assert gateway.request.call_args.kwargs["json"] == {
        "doc_type": "E",
        "subject": "Lo aprobado",
    }


@pytest.mark.asyncio
async def test_una_propuesta_no_se_ejecuta_dos_veces(client_with_gateway) -> None:
    client, gateway = client_with_gateway
    gateway.request.return_value = httpx.Response(201, json={})
    cabeceras = {"Authorization": "Bearer tok", "X-Tenant-Slug": "demo"}

    propuesta = await client.post(
        "/api/v1/mcp/tools/radicar_documento/invoke",
        json={"doc_type": "E", "subject": "Tutela"},
        headers=cabeceras,
    )
    token = propuesta.json()["proposal"]["token"]

    primera = await client.post(
        "/api/v1/mcp/proposals/execute", json={"token": token}, headers=cabeceras
    )
    segunda = await client.post(
        "/api/v1/mcp/proposals/execute", json={"token": token}, headers=cabeceras
    )

    assert primera.status_code == 200
    assert segunda.status_code == 409
    assert segunda.json()["detail"]["error"] == "propuesta_consumida"
    assert gateway.request.await_count == 1  # un solo radicado, no dos


@pytest.mark.asyncio
async def test_un_token_inventado_no_ejecuta_nada(client_with_gateway) -> None:
    client, gateway = client_with_gateway
    resp = await client.post(
        "/api/v1/mcp/proposals/execute",
        json={"token": "op1.YWJj.ZGVm"},
        headers={"Authorization": "Bearer tok", "X-Tenant-Slug": "demo"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "propuesta_invalida"
    gateway.request.assert_not_called()


@pytest.mark.asyncio
async def test_si_le_retiran_el_permiso_entre_proponer_y_confirmar_no_se_ejecuta(
    client_with_gateway,
) -> None:
    """La propuesta acredita QUÉ se aprobó, no que quien la presenta siga pudiendo.

    Confundir esas dos cosas convierte el token en un salvoconducto.
    """
    client, gateway = client_with_gateway
    gateway.request.return_value = httpx.Response(201, json={})
    cabeceras = {"Authorization": "Bearer tok", "X-Tenant-Slug": "demo"}

    propuesta = await client.post(
        "/api/v1/mcp/tools/radicar_documento/invoke",
        json={"doc_type": "E", "subject": "Tutela"},
        headers=cabeceras,
    )
    token = propuesta.json()["proposal"]["token"]

    # Le retiran PERM_RADI mientras leía la tarjeta.
    async def _sin_permisos(path: str, **kwargs: object):
        if path == "/api/v1/auth/me":
            return httpx.Response(
                200,
                json={
                    "user_id": "11111111-1111-1111-1111-111111111111",
                    "tenant_slug": "demo",
                    "roles": [],
                    "permissions": [],
                    "permission_levels": {},
                },
            )
        return httpx.Response(200, json={})

    gateway.get.side_effect = _sin_permisos

    resp = await client.post(
        "/api/v1/mcp/proposals/execute", json={"token": token}, headers=cabeceras
    )

    assert resp.status_code == 403
    gateway.request.assert_not_called()


def test_la_cabecera_fantasma_no_se_lee_en_ninguna_parte_del_codigo() -> None:
    """El control ESTRUCTURAL, porque el de comportamiento no discriminaba.

    Se comprobó por mutación: volver a leer `X-User-Permissions` en
    `list_tools` —sumando lo que traiga al conjunto real— dejaba la suite entera
    en verde. El test de comportamiento pasaba, pero **por otra razón**: el
    permiso forjado llega sin su nivel, y `visible_tools` compara también el
    nivel (`levels.get(p, 0)`), así que el permiso colado no alcanzaba ninguna
    tool. Defensa en profundidad real, y a la vez un test que afirmaba una cosa
    y estaba comprobando otra. Los dos defectos se habrían compuesto en cuanto
    alguien tocara `levels`.

    Lo que hay que fijar es que la cabecera **no se lea**, y eso es una
    afirmación sobre la declaración, no sobre el resultado. Se inspecciona el
    AST y no el texto: este archivo y los módulos que audita están llenos de
    comentarios que la nombran para explicar por qué se retiró, y un `grep`
    encontraría esas explicaciones y daría el hallazgo por bueno — el mismo
    tropiezo que ya se cazó una vez buscando una cadena en el propio docstring.
    """
    import ast
    from pathlib import Path

    prohibidos = {
        "x_user_permissions",
        "x-user-permissions",
        "mcp_user_permissions",
        "mcp_user_roles",
    }
    raiz = Path(__file__).resolve().parents[1] / "app"
    hallazgos: list[str] = []

    for fichero in sorted(raiz.rglob("*.py")):
        arbol = ast.parse(fichero.read_text(encoding="utf-8"))
        # Los docstrings son `Expr(Constant)`: se descartan para no confundir una
        # explicación con un uso.
        docstrings = {
            id(n.value)
            for n in ast.walk(arbol)
            if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
        }
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.arg) and nodo.arg in prohibidos:
                hallazgos.append(f"{fichero.name}: parámetro '{nodo.arg}'")
            elif isinstance(nodo, ast.Attribute) and nodo.attr in prohibidos:
                hallazgos.append(f"{fichero.name}: atributo '{nodo.attr}'")
            elif isinstance(nodo, ast.Name) and nodo.id in prohibidos:
                hallazgos.append(f"{fichero.name}: nombre '{nodo.id}'")
            elif (
                isinstance(nodo, ast.Constant)
                and isinstance(nodo.value, str)
                and nodo.value.lower() in prohibidos
                and id(nodo) not in docstrings
            ):
                hallazgos.append(f"{fichero.name}: literal '{nodo.value}'")

    assert not hallazgos, (
        "la identidad volvió a leerse de una cabecera o de un ajuste de entorno "
        "que el backend no consulta; sale de `/auth/me` y de ningún otro sitio: "
        f"{hallazgos}"
    )
