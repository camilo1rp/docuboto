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

_UID = "11111111-1111-1111-1111-111111111111"


def _cliente_con_identidad(permisos=("PERM_RADI",), niveles=None, roles=()):
    """Doble del gateway que responde `/auth/me` y registra los despachos.

    `get` se distingue de `request` a propósito: resolver identidad es una
    lectura del propio mcp-server, y confundirla con un despacho dejaría sin
    sentido los `assert_not_called` que sostienen este archivo.

    **Los permisos se declaran AQUÍ, y ya no hay otra manera.** Antes se
    fabricaban con `X-User-Permissions` o con `MCP_USER_PERMISSIONS`, y ese era
    justamente el problema: la cabecera no la produce nadie y el ajuste de
    entorno no lo consulta el backend, de modo que los tests pasaban con una
    autorización que en producción no existía. Un doble de `/auth/me` puede
    mentir sobre *quién* es el usuario, pero ya no sobre *por dónde* se entera
    el servidor.
    """
    from unittest.mock import AsyncMock, MagicMock

    me = MagicMock()
    me.status_code = 200
    me.json.return_value = {
        "user_id": _UID,
        "tenant_slug": "demo",
        "permissions": list(permisos),
        "permission_levels": (
            dict(niveles) if niveles is not None else {p: 3 for p in permisos}
        ),
        "roles": list(roles),
    }
    client = AsyncMock()
    client.get.return_value = me
    return client




@pytest.mark.asyncio
async def test_build_tools_have_mcp_schema() -> None:
    from app import mcp_app

    tools = await mcp_app.build_tools(_cliente_con_identidad())
    names = {t.name for t in tools}
    assert "radicar_documento" in names and "buscar_radicados" in names
    # El marco de alcance se ofrece SIEMPRE, tenga o no permisos: es la mitad
    # que decía qué NO se puede y por dónde sí.
    assert mcp_app.MI_ALCANCE in names
    # Cada tool MCP tiene inputSchema; las de ruta declaran su parámetro requerido.
    by_name = {t.name: t for t in tools}
    assert by_name["consultar_radicado"].inputSchema["required"] == ["document_id"]
    assert by_name["radicar_documento"].inputSchema["type"] == "object"


@pytest.mark.asyncio
async def test_build_tools_scope_sin_permiso() -> None:
    from app import mcp_app

    names = {t.name for t in await mcp_app.build_tools(_cliente_con_identidad(permisos=()))}
    assert "radicar_documento" not in names  # exige PERM_RADI
    assert "buscar_radicados" in names  # lectura
    assert mcp_app.MI_ALCANCE in names  # y sigue pudiendo saber por qué


@pytest.mark.asyncio
async def test_el_nivel_de_solo_lectura_no_muestra_las_escrituras() -> None:
    """`build_tools` no pasaba `levels`, así que este caso no se comprobaba.

    La tool es `tipificar_radicado`, no `radicar_documento`: **radicar exige
    nivel 1**, igual que su endpoint, y escribir el test contra ella lo habría
    dejado exigiendo de más — el mismo defecto al revés, escondiendo a quien sí
    puede. Tipificar es de las pocas que sí exige nivel de creación, porque fija
    la serie TRD que gobierna la retención del documento.

    Con nivel de consulta y la tool ofrecida, la persona la pide, el backend
    responde 403, y el límite se descubre fallando.
    """
    from app import mcp_app

    solo_lectura = _cliente_con_identidad(permisos=("PERM_RADI",), niveles={"PERM_RADI": 1})
    names = {t.name for t in await mcp_app.build_tools(solo_lectura)}
    assert "tipificar_radicado" not in names
    assert "radicar_documento" in names  # exige 1: ofrecerla es correcto
    assert "buscar_radicados" in names

    # Control positivo: con nivel de creación sí aparece. Sin él, un
    # `visible_tools` que negara todo dejaría el test verde y el catálogo vacío.
    con_nivel = _cliente_con_identidad(permisos=("PERM_RADI",), niveles={"PERM_RADI": 3})
    assert "tipificar_radicado" in {t.name for t in await mcp_app.build_tools(con_nivel)}


@pytest.mark.asyncio
async def test_call_dispatches_to_gateway(monkeypatch) -> None:
    from app import mcp_app
    from app.core.config import settings

    monkeypatch.setattr(settings, "mcp_bearer_token", "tok")
    monkeypatch.setattr(settings, "mcp_tenant_slug", "demo")
    client = _cliente_con_identidad()
    client.request.return_value = httpx.Response(200, json={"total": 2})

    text = await mcp_app.call("buscar_radicados", {"q": "tutela"}, client)
    assert '"total": 2' in text or '"total":2' in text
    call = client.request.call_args
    assert call.args[0] == "GET"
    assert call.args[1] == "/api/v1/search"
    assert call.kwargs["headers"]["Authorization"] == "Bearer tok"
    assert call.kwargs["headers"]["X-Tenant-Slug"] == "demo"


@pytest.mark.asyncio
async def test_call_write_without_permission_denied() -> None:
    from app import mcp_app
    from app.services.dispatcher import DispatchError

    with pytest.raises(DispatchError) as ei:
        await mcp_app.call(
            "radicar_documento",
            {"doc_type": "E", "subject": "x"},
            _cliente_con_identidad(permisos=()),
        )
    assert ei.value.code == "permiso_insuficiente"


@pytest.mark.asyncio
async def test_call_unknown_tool(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    from app import mcp_app
    from app.services.dispatcher import DispatchError

    with pytest.raises(DispatchError) as ei:
        await mcp_app.call("inexistente", {}, AsyncMock())
    assert ei.value.code == "tool_not_found"


def test_el_contexto_http_solo_transporta_identidad_no_permisos() -> None:
    """El contexto por cabeceras (HTTP) prevalece sobre el entorno (stdio)...

    ...y ya solo lleva el bearer y el tenant. Llevaba también los permisos, que
    venían de `X-User-Permissions`: una cabecera que ningún componente produce y
    que el gateway descarta por forjable. Que ya no pueda transportarlos es la
    corrección, no un detalle de firma: mientras el transporte pudiera afirmar
    qué puede hacer alguien, la afirmación era del cliente.
    """
    import inspect

    from app import mcp_app

    firma = set(inspect.signature(mcp_app.set_context).parameters)
    assert firma == {"authorization", "tenant"}, (
        "el contexto de transporte no puede volver a llevar permisos ni roles: "
        f"parámetros actuales {sorted(firma)}"
    )

    tok = mcp_app.set_context(authorization="Bearer h", tenant="t1")
    try:
        assert mcp_app._session()["authorization"] == "Bearer h"
        assert mcp_app._session()["tenant"] == "t1"
        assert "permissions" not in mcp_app._session()
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
            # El MISMO transporte bajo el prefijo que el gateway enruta. Sin
            # esto, la entrada nueva de la tabla de ruteo apuntaría a una ruta
            # que no existe y el cliente externo recibiría un 404 del propio
            # mcp-server — el fallo de la carga masiva, un salto más allá.
            por_gateway = await ac.post("/api/v1/mcp/stream/", json=body, headers=headers)
    assert resp.status_code == 200
    assert "protocolVersion" in resp.text and "orpycamcp" in resp.text.lower()
    assert por_gateway.status_code == 200
    assert "protocolVersion" in por_gateway.text


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

    from app import mcp_app
    from app.core.config import settings

    monkeypatch.setattr(settings, "mcp_bearer_token", "tok")
    monkeypatch.setattr(settings, "mcp_tenant_slug", "demo")
    monkeypatch.setattr(settings, "mcp_proposal_secret", "clave-de-pruebas")

    client = _cliente_con_identidad()
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


@pytest.mark.asyncio
async def test_el_binding_confirma_la_propuesta_que_el_mismo_emitio(monkeypatch) -> None:
    """El ciclo cerrado por el transporte MCP, sin pasar por la interfaz web.

    Es el test que destapó que la propuesta emitida por este transporte nacía
    muerta: se firmaba con `s.get("sub")`, una clave que nunca existió en el
    diccionario de sesión, así que el `sub` iba vacío y la verificación la
    rechazaba después como ajena. La propuesta se veía impecable y no había
    manera de ejecutarla — el fallo solo aparecía al pulsar el botón.
    """
    import json as _json
    from unittest.mock import MagicMock

    from app import mcp_app
    from app.core.config import settings
    from app.services import proposals

    monkeypatch.setattr(settings, "mcp_bearer_token", "tok")
    monkeypatch.setattr(settings, "mcp_tenant_slug", "demo")
    monkeypatch.setattr(settings, "mcp_proposal_secret", "clave-de-pruebas")
    proposals.limpiar_consumidas()

    client = _cliente_con_identidad()
    creado = MagicMock()
    creado.status_code = 201
    creado.json.return_value = {"tracking_number": "2026-DEMO-E-000001"}
    client.request.return_value = creado

    propuesta = _json.loads(
        await mcp_app.call("radicar_documento", {"doc_type": "E", "subject": "Tutela"}, client)
    )
    resultado = _json.loads(
        await mcp_app.call(
            mcp_app.CONFIRMAR, {"token": propuesta["proposal"]["token"]}, client
        )
    )

    assert resultado["ejecutado"] is True
    assert resultado["data"]["tracking_number"] == "2026-DEMO-E-000001"
    client.request.assert_called_once()


@pytest.mark.asyncio
async def test_sin_escrituras_al_alcance_no_se_ofrece_confirmar() -> None:
    """Ofrecer «confirmar» a quien no puede escribir sugiere un poder que no tiene."""
    from app import mcp_app

    nombres = {t.name for t in await mcp_app.build_tools(_cliente_con_identidad(permisos=()))}
    assert mcp_app.CONFIRMAR not in nombres

    con_permiso = await mcp_app.build_tools(_cliente_con_identidad())
    assert mcp_app.CONFIRMAR in {t.name for t in con_permiso}


@pytest.mark.asyncio
async def test_las_escrituras_se_anuncian_como_tales_en_las_anotaciones() -> None:
    """`readOnlyHint`/`destructiveHint`: pistas para el cliente, no controles.

    Se comprueban porque un cliente que las honra pone su propia fricción antes
    de radicar. Que sean solo pistas es lo que obliga a que la garantía siga
    viviendo en la propuesta firmada — este test no releva a los otros.
    """
    from app import mcp_app

    por_nombre = {t.name: t for t in await mcp_app.build_tools(_cliente_con_identidad())}

    radicar = por_nombre["radicar_documento"]
    assert radicar.annotations.readOnlyHint is False
    assert radicar.annotations.destructiveHint is True

    consulta = por_nombre["consultar_radicado"]
    assert consulta.annotations.readOnlyHint is True
    assert consulta.annotations.destructiveHint is False


class _SesionFalsa:
    """Doble de sesión MCP: anuncia elicitación y responde lo que se le diga."""

    def __init__(self, accion: str, confirmar: bool = True, capaz: bool = True) -> None:
        self.accion = accion
        self.confirmar = confirmar
        self.capaz = capaz
        self.mensajes: list[str] = []

    def check_client_capability(self, _cap) -> bool:
        return self.capaz

    async def elicit(self, message: str, requestedSchema: dict):
        import mcp.types as types

        self.mensajes.append(message)
        return types.ElicitResult(action=self.accion, content={"confirmar": self.confirmar})


def _con_sesion(monkeypatch, sesion):
    """Inyecta la sesión en el `request_context` del server MCP."""
    from unittest.mock import MagicMock

    from app import mcp_app

    ctx = MagicMock()
    ctx.session = sesion
    monkeypatch.setattr(type(mcp_app.server), "request_context", property(lambda self: ctx))


def _preparar_binding(monkeypatch):
    from app.core.config import settings
    from app.services import proposals

    monkeypatch.setattr(settings, "mcp_bearer_token", "tok")
    monkeypatch.setattr(settings, "mcp_tenant_slug", "demo")
    monkeypatch.setattr(settings, "mcp_proposal_secret", "clave-de-pruebas")
    proposals.limpiar_consumidas()


@pytest.mark.asyncio
async def test_la_elicitacion_pregunta_con_el_texto_del_catalogo(monkeypatch) -> None:
    """Aprobar por elicitación ejecuta — y la pregunta la redacta el catálogo.

    Lo segundo no es cosmética: si el mensaje lo compusiera el modelo, podría
    describir un acto distinto del que va firmado en el token, y la persona
    estaría aprobando una cosa creyendo aprobar otra.
    """
    import json as _json
    from unittest.mock import MagicMock

    from app import mcp_app

    _preparar_binding(monkeypatch)
    sesion = _SesionFalsa("accept", confirmar=True)
    _con_sesion(monkeypatch, sesion)

    client = _cliente_con_identidad()
    creado = MagicMock()
    creado.status_code = 201
    creado.json.return_value = {"tracking_number": "2026-DEMO-E-000002"}
    client.request.return_value = creado

    cuerpo = _json.loads(
        await mcp_app.call("radicar_documento", {"doc_type": "E", "subject": "Tutela"}, client)
    )

    assert cuerpo["ejecutado"] is True
    assert "Tutela" in sesion.mensajes[0]
    client.request.assert_called_once()


@pytest.mark.asyncio
async def test_rechazar_en_la_elicitacion_no_ejecuta_nada(monkeypatch) -> None:
    """El camino que de verdad importa: decir que no tiene que no hacer nada."""
    import json as _json

    from app import mcp_app

    _preparar_binding(monkeypatch)
    _con_sesion(monkeypatch, _SesionFalsa("decline"))

    client = _cliente_con_identidad()
    cuerpo = _json.loads(
        await mcp_app.call("radicar_documento", {"doc_type": "E", "subject": "Tutela"}, client)
    )

    assert cuerpo["ejecutado"] is False
    assert cuerpo["kind"] == "cancelled"
    client.request.assert_not_called()


@pytest.mark.asyncio
async def test_aceptar_sin_marcar_la_casilla_no_ejecuta(monkeypatch) -> None:
    """`accept` con la casilla en falso es un NO, no un sí.

    Sin este test, leer solo `action` daría el acto por aprobado en cuanto el
    cliente cerrara el formulario.
    """
    import json as _json

    from app import mcp_app

    _preparar_binding(monkeypatch)
    _con_sesion(monkeypatch, _SesionFalsa("accept", confirmar=False))

    client = _cliente_con_identidad()
    cuerpo = _json.loads(
        await mcp_app.call("radicar_documento", {"doc_type": "E", "subject": "Tutela"}, client)
    )

    assert cuerpo["ejecutado"] is False
    client.request.assert_not_called()


@pytest.mark.asyncio
async def test_sin_canal_de_vuelta_queda_el_camino_de_dos_tools(monkeypatch) -> None:
    """Límite declarado: en HTTP streamable no hay a quién preguntar.

    Y ante la duda no se ejecuta: se devuelve la propuesta, que es el camino que
    exige una segunda llamada con el token.
    """
    import json as _json

    from app import mcp_app

    _preparar_binding(monkeypatch)
    _con_sesion(monkeypatch, _SesionFalsa("accept", capaz=False))

    client = _cliente_con_identidad()
    cuerpo = _json.loads(
        await mcp_app.call("radicar_documento", {"doc_type": "E", "subject": "Tutela"}, client)
    )

    assert cuerpo["kind"] == "proposal"
    assert cuerpo["ejecutado"] is False
    client.request.assert_not_called()


@pytest.mark.asyncio
async def test_el_marco_de_alcance_dice_lo_que_NO_se_puede_y_por_donde_si() -> None:
    """La mitad que faltaba: los permisos escondían, y esconder no explica.

    Un catálogo recortado le dice a la persona lo que puede hacer y **calla** lo
    demás, así que el límite se descubre pidiéndolo: el asistente lo intenta,
    llega un 403, o —peor— la persona espera una acción que con sus credenciales
    nunca iba a ocurrir. El marco declara las tres listas, y en la tercera pone
    el motivo y la vía alternativa.
    """
    import json as _json

    from app import mcp_app

    # Un operador de trámite: puede tramitar, no puede radicar ni expedientar.
    cliente = _cliente_con_identidad(permisos=("PERM_TRAMITAR",))
    marco = _json.loads(await mcp_app.call(mcp_app.MI_ALCANCE, {}, cliente))

    puede = {e["herramienta"] for e in marco["puede_hacer"]}
    no_puede = {e["herramienta"]: e for e in marco["no_puede"]}

    assert "asignar_radicado" in puede
    assert "radicar_documento" in no_puede
    entrada = no_puede["radicar_documento"]
    # Operación de DOMINIO, no el nombre de la tool: el marco habla el idioma
    # del trabajo o no sirve para decírselo a nadie.
    assert entrada["operacion"] != "radicar_documento"
    assert "PERM_RADI" in entrada["motivo"]
    # Y la parte útil: negar sin más deja a la persona igual de atascada.
    assert "correspondencia" in entrada["via_alternativa"]

    # Precisiones que el sistema NO distingue, dichas para que nadie las afirme.
    assert marco["limites_declarados"]


@pytest.mark.asyncio
async def test_el_recurso_de_alcance_y_la_tool_dan_el_mismo_marco(monkeypatch) -> None:
    """Dos puertas al mismo dato, no dos versiones del alcance.

    El recurso existe para que un cliente MCP lo cargue como contexto al abrir la
    conversación, sin gastar un turno ni depender de que el modelo se acuerde de
    preguntar. Lo que no puede pasar es que respondan cosas distintas: un marco
    que dependa de por dónde se pregunte es peor que no tenerlo.
    """
    import json as _json

    from app import mcp_app, mcp_resources

    cliente = _cliente_con_identidad(permisos=("PERM_TRAMITAR",))

    class _CM:
        async def __aenter__(self):
            return cliente

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(mcp_resources.httpx, "AsyncClient", lambda **kw: _CM())

    por_recurso = _json.loads(await mcp_resources._read_resource("orfeo://alcance"))
    por_tool = _json.loads(await mcp_app.call(mcp_app.MI_ALCANCE, {}, cliente))
    assert por_recurso == por_tool

    uris = {str(r.uri) for r in await mcp_resources._list_resources()}
    assert "orfeo://alcance" in uris, "el recurso no se anuncia: nadie lo encontraría"
