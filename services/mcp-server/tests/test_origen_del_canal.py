# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""AREA-11-04 / AREA-11-08: lo que el mcp-server dice de sí mismo al gateway.

- El MCP actúa EN NOMBRE de una persona pero entra al gateway por
  `/api/v1/<dominio>`: sin una marca de origen verificable, lo que confirma la
  tarjeta quedaba asentado como canal 'api'.
- Todas las llamadas del mcp-server de todas las entidades salían con la IP del
  contenedor y compartían su cubo por IP del limitador.
"""

from __future__ import annotations

import httpx
import pytest
from orpycamcp_common import identidad

from app.core.contexto import ContextoEntranteMiddleware
from app.services import dispatcher, origen
from app.services.identity import resolve_identity
from app.services.scope import build_scope

SECRETO = "secreto-de-prueba-del-origen"
TOKEN = "Bearer token-de-ana"


@pytest.fixture
def con_secreto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_ORIGIN_SECRET", SECRETO)


async def _enviar(prep: dispatcher.PreparedCall | None = None, **kw) -> httpx.Request:
    visto: dict[str, httpx.Request] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        visto["r"] = request
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_handler), base_url="http://gw"
    ) as client:
        await dispatcher.execute(
            client,
            prep or dispatcher.PreparedCall("GET", "/api/v1/x", {}, None),
            authorization=kw.get("authorization", TOKEN),
            tenant_slug=kw.get("tenant_slug", "demo"),
            idempotency_key=kw.get("idempotency_key"),
        )
    return visto["r"]


# -- AREA-11-04 (1): la marca de origen ------------------------------------------


@pytest.mark.asyncio
async def test_toda_llamada_lleva_la_marca_y_el_gateway_la_acepta(con_secreto) -> None:
    req = await _enviar()

    marca = req.headers.get(identidad.CABECERA_ORIGEN)
    assert marca, "el despacho no lleva marca de origen"
    assert (
        identidad.verificar_origen(marca, tenant="demo", authorization=TOKEN) == "mcp"
    )


@pytest.mark.asyncio
async def test_la_marca_no_sirve_con_otro_token_ni_otra_entidad(con_secreto) -> None:
    marca = (await _enviar()).headers[identidad.CABECERA_ORIGEN]

    assert identidad.verificar_origen(marca, tenant="demo", authorization="Bearer otro") is None
    assert identidad.verificar_origen(marca, tenant="otra", authorization=TOKEN) is None


@pytest.mark.asyncio
async def test_sin_secreto_no_se_emite_marca(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-closed: sin secreto no hay marca, y el gateway asentaría 'api'."""
    monkeypatch.delenv("MCP_ORIGIN_SECRET", raising=False)

    req = await _enviar()

    assert identidad.CABECERA_ORIGEN not in req.headers


@pytest.mark.asyncio
async def test_las_lecturas_de_identidad_y_marco_tambien_la_llevan(con_secreto) -> None:
    """`/auth/me` y el marco comparten el cubo de la persona: salen por el mismo sitio."""
    vistas: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        vistas.append(request)
        if request.url.path == "/api/v1/auth/me":
            return httpx.Response(200, json={"user_id": "u-1", "permissions": []})
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_handler), base_url="http://gw"
    ) as client:
        ident = await resolve_identity(client, authorization=TOKEN, tenant_slug="demo")
        await build_scope(client, ident, authorization=TOKEN, tenant_slug="demo")

    rutas = {r.url.path for r in vistas}
    assert {"/api/v1/auth/me", "/api/v1/auth/clearance", "/api/v1/auth/context"} <= rutas
    for request in vistas:
        assert identidad.CABECERA_ORIGEN in request.headers, request.url.path


# -- AREA-11-04 (2): la correlación con la propuesta -----------------------------


@pytest.mark.asyncio
async def test_el_request_id_y_la_idempotency_key_viajan(con_secreto) -> None:
    from orpycamcp_common import request_log

    token = request_log._request_id.set("rid-del-turno-1")
    try:
        req = await _enviar(idempotency_key="jti-de-la-propuesta")
    finally:
        request_log._request_id.reset(token)

    assert req.headers["x-request-id"] == "rid-del-turno-1"
    assert req.headers["idempotency-key"] == "jti-de-la-propuesta"


# -- AREA-11-08: la IP del cliente real ------------------------------------------


@pytest.mark.asyncio
async def test_execute_reenvia_la_ip_del_cliente_entrante(con_secreto) -> None:
    token = origen.fijar_ip_cliente("198.51.100.23")
    try:
        req = await _enviar()
    finally:
        origen.soltar_ip_cliente(token)

    assert req.headers["x-forwarded-for"] == "198.51.100.23"


@pytest.mark.asyncio
async def test_sin_ip_entrante_no_se_inventa_una(con_secreto) -> None:
    assert "x-forwarded-for" not in (await _enviar()).headers


# ADR-036: la identidad firmada va atada al método y la ruta de la petición.
_METODO = "GET"
_RUTA = "/api/v1/mcp/tools"


async def _correr_middleware(cabeceras: dict[str, str]) -> str | None:
    """Pasa una petición por el middleware y devuelve la IP que vio el manejador."""
    visto: dict[str, str | None] = {}

    async def app(scope, receive, send):  # noqa: ANN001
        visto["ip"] = origen._ip_cliente.get()

    mw = ContextoEntranteMiddleware(app)
    scope = {
        "type": "http",
        "method": _METODO,
        "path": _RUTA,
        "headers": [(k.lower().encode(), v.encode()) for k, v in cabeceras.items()],
    }
    await mw(scope, None, None)
    return visto["ip"]


def _identidad_firmada(ip: str) -> dict[str, str]:
    base = {
        "X-User-Id": "u1",
        "X-Username": "ana",
        "X-Tenant-Slug": "demo",
        "X-User-Roles": "",
        "X-Canal": "api",
        "X-Real-Ip": ip,
    }
    return identidad.firmar_ip_real(identidad.firmar(base, metodo=_METODO, ruta=_RUTA))


@pytest.mark.asyncio
async def test_el_middleware_toma_la_ip_solo_si_el_gateway_la_firmo(con_secreto) -> None:
    assert await _correr_middleware(_identidad_firmada("198.51.100.23")) == "198.51.100.23"


@pytest.mark.asyncio
async def test_el_middleware_ignora_una_ip_sin_firma_o_adulterada(con_secreto) -> None:
    sin_firma = {"X-Real-Ip": "6.6.6.6", "X-User-Id": "u1"}
    adulterada = {**_identidad_firmada("198.51.100.23"), "X-Real-Ip": "6.6.6.6"}

    assert await _correr_middleware(sin_firma) is None
    assert await _correr_middleware(adulterada) is None


@pytest.mark.asyncio
async def test_sin_secreto_el_middleware_no_confia_en_ninguna_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_ORIGIN_SECRET", raising=False)

    assert await _correr_middleware({"X-Real-Ip": "6.6.6.6"}) is None


# -- AREA-11-04 (2): el `jti` de la propuesta viaja como Idempotency-Key ---------


@pytest.mark.asyncio
async def test_confirmar_una_propuesta_envia_su_jti_como_idempotency_key(
    con_secreto, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sin esto la «correlación» del asiento con la propuesta era una frase del docstring:
    `dispatch` ni siquiera aceptaba la clave."""
    from app.core.config import settings
    from app.services import write_gate
    from app.services.identity import Identity

    monkeypatch.setattr(settings, "mcp_proposal_secret", "clave-de-propuestas-de-prueba")
    ident = Identity(
        user_id="u-1", tenant_slug="demo", permissions={"PERM_RADI"}, levels={"PERM_RADI": 3}
    )
    tarjeta = write_gate.preparar(
        write_gate.get_tool("radicar_documento"),
        {"doc_type": "I", "subject": "asunto de prueba"},
        sub=ident.user_id,
        ten=ident.tenant_slug,
    )
    visto: dict[str, httpx.Request] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        visto["r"] = request
        return httpx.Response(201, json={"tracking_number": "2026-DEMO-I-000001"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_handler), base_url="http://gw"
    ) as client:
        resultado = await write_gate.ejecutar(
            client,
            tarjeta["proposal"]["token"],
            identidad=ident,
            authorization=TOKEN,
            tenant_slug="demo",
            frase="CONFIRMO",
        )

    assert resultado["ejecutado"] is True
    assert visto["r"].headers["idempotency-key"] == tarjeta["proposal"]["id"]
    assert identidad.verificar_origen(
        visto["r"].headers[identidad.CABECERA_ORIGEN], tenant="demo", authorization=TOKEN
    ) == "mcp"


# -- Guardián: ninguna llamada saliente arma sus cabeceras a mano -----------------


def test_ninguna_llamada_al_gateway_arma_sus_cabeceras_a_mano() -> None:
    """Cada módulo que armara su propio `{"Authorization": ..., "X-Tenant-Slug": ...}`
    se saltaría la marca de origen, el X-Request-Id y la IP del cliente: el canal
    'mcp' y la cuota por IP fallarían solo en esa ruta (la que menos se prueba)."""
    import re
    from pathlib import Path

    app_dir = Path(origen.__file__).resolve().parents[1]
    patron = re.compile(r"""["'](?:Authorization|X-Tenant-Slug)["']\s*:""")
    ofensores = [
        str(p.relative_to(app_dir))
        for p in app_dir.rglob("*.py")
        if p.name != "origen.py" and patron.search(p.read_text(encoding="utf-8"))
    ]
    assert not ofensores, f"cabeceras al gateway armadas fuera de origen.py: {ofensores}"
    assert any(app_dir.rglob("dispatcher.py")), "control positivo: el árbol no se leyó"


@pytest.mark.asyncio
async def test_la_marca_va_ligada_al_token_de_cada_llamada(con_secreto) -> None:
    """Dos personas, dos marcas: la de una no valida con el token de la otra."""
    a = (await _enviar(authorization="Bearer a")).headers[identidad.CABECERA_ORIGEN]
    b = (await _enviar(authorization="Bearer b")).headers[identidad.CABECERA_ORIGEN]

    assert a != b
    assert identidad.verificar_origen(a, tenant="demo", authorization="Bearer b") is None


@pytest.mark.asyncio
async def test_confirmar_un_procedimiento_envia_el_jti_a_su_ejecutor(
    con_secreto, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La rama de procedimientos de `write_gate.ejecutar` es OTRA llamada: la
    correlación de la rama de una sola tool no la cubre."""
    from app.core.config import settings
    from app.services import procedure_runner, write_gate
    from app.services.identity import Identity
    from app.tools.procedures import get_procedimiento

    monkeypatch.setattr(settings, "mcp_proposal_secret", "clave-de-propuestas-de-prueba")
    ident = Identity(user_id="u-1", tenant_slug="demo", permissions=set(), is_root=True)
    proc = get_procedimiento("tramitar_peticion_ciudadana")
    tarjeta = write_gate.preparar(
        proc,
        {"subject": "P", "sender_name": "Ana", "dest_dept_code": 902, "to_dept_code": 902},
        sub=ident.user_id,
        ten=ident.tenant_slug,
    )
    visto: dict[str, object] = {}

    async def _falso(client, tool, args, **kw):  # noqa: ANN001
        visto.update(kw)
        return {"completado": True, "pasos": []}

    monkeypatch.setattr(procedure_runner, "ejecutar", _falso)
    async with httpx.AsyncClient(base_url="http://gw") as client:
        await write_gate.ejecutar(
            client,
            tarjeta["proposal"]["token"],
            identidad=ident,
            authorization=TOKEN,
            tenant_slug="demo",
            frase="CONFIRMO",
        )

    assert visto["idempotency_key"] == tarjeta["proposal"]["id"]
