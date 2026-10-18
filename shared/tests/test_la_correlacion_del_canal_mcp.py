# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""AREA-11-04: lo que entra por el asistente deja en el asiento cómo se correlaciona.

Un radicado creado desde la tarjeta del asistente quedaba en `audit_log` igual que una
llamada directa: ni canal, ni forma de atarlo al turno ni a la propuesta firmada que lo
autorizó. Con canal 'mcp' el asiento lleva `payload.correlacion` (`request_id` del
gateway e `Idempotency-Key`, que es el `jti` de la propuesta). El resto de canales NO
cambia de contenido.

Se prueba también la pieza que la produce: `RequestLogMiddleware` captura la cabecera
`Idempotency-Key` durante la petición y la suelta después.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from orpycamcp_common import audit, identidad, request_log


class _Tx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _conn() -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval.return_value = None
    conn.transaction = MagicMock(return_value=_Tx())
    return conn


async def _asentar(canal: str, payload: dict | None = None) -> dict:
    conn = _conn()
    await audit.append(
        conn,
        tenant_slug="demo",
        service="document-service",
        action="radicado.creado",
        object_type="radicado",
        canal=canal,
        payload=payload or {"a": 1},
    )
    insert = conn.execute.await_args_list[-1]
    return json.loads(next(a for a in insert.args if isinstance(a, str) and a.startswith("{")))


@pytest.fixture
def en_peticion():
    t1 = request_log._request_id.set("rid-turno-7")
    t2 = request_log._idempotency_key.set("jti-propuesta-9")
    yield
    request_log._request_id.reset(t1)
    request_log._idempotency_key.reset(t2)


@pytest.mark.asyncio
async def test_el_canal_mcp_lleva_la_correlacion(en_peticion) -> None:
    payload = await _asentar("mcp")

    assert payload["correlacion"] == {
        "request_id": "rid-turno-7",
        "idempotency_key": "jti-propuesta-9",
    }
    assert payload["a"] == 1


@pytest.mark.asyncio
async def test_canal_peticion_hereda_el_de_la_peticion(en_peticion) -> None:
    """El defecto de partida: el acto que el asistente confirma se asentaba 'api'.
    Hoy el llamante declara `CANAL_PETICION` y hereda el que derivó el gateway."""
    token = request_log._canal.set("mcp")
    try:
        conn = _conn()
        await audit.append(
            conn,
            tenant_slug="demo",
            service="document-service",
            action="radicado.creado",
            object_type="radicado",
            canal=audit.CANAL_PETICION,
        )
    finally:
        request_log._canal.reset(token)

    insert = conn.execute.await_args_list[-1]
    assert "mcp" in insert.args
    assert "api" not in insert.args


@pytest.mark.asyncio
async def test_el_canal_explicito_manda_sobre_el_de_la_peticion(en_peticion) -> None:
    token = request_log._canal.set("mcp")
    try:
        conn = _conn()
        await audit.append(
            conn,
            tenant_slug="demo",
            service="s",
            action="a",
            object_type="o",
            canal="system",
        )
    finally:
        request_log._canal.reset(token)

    assert "system" in conn.execute.await_args_list[-1].args


@pytest.mark.asyncio
async def test_el_middleware_solo_hereda_canales_de_persona() -> None:
    visto: list[str] = []

    async def app(scope, receive, send):  # noqa: ANN001
        visto.append(request_log.canal_actual())

    mw = request_log.RequestLogMiddleware(app, servicio="t")
    for valor, esperado in ((b"mcp", "mcp"), (b"api", "api"), (b"system", ""), (b"web", ""), (b"", "")):
        scope = {"type": "http", "method": "GET", "path": "/", "headers": [(b"x-canal", valor)]}
        await mw(scope, None, None)
        assert visto[-1] == esperado
    assert request_log.canal_actual() == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("canal", ["api", "ui", "system"])
async def test_los_otros_canales_no_cambian_de_contenido(canal: str, en_peticion) -> None:
    assert await _asentar(canal) == {"a": 1}


@pytest.mark.asyncio
async def test_fuera_de_una_peticion_no_se_inventa_correlacion() -> None:
    assert await _asentar("mcp") == {"a": 1}


@pytest.mark.asyncio
async def test_el_middleware_captura_la_idempotency_key_y_la_suelta() -> None:
    visto: dict[str, str] = {}

    async def app(scope, receive, send):  # noqa: ANN001
        visto["idem"] = request_log.idempotency_key_actual()
        visto["rid"] = request_log.request_id_actual()

    mw = request_log.RequestLogMiddleware(app, servicio="t")
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/x",
        "headers": [(b"idempotency-key", b"jti-1"), (b"x-request-id", b"rid-1")],
    }
    await mw(scope, None, None)

    assert visto == {"idem": "jti-1", "rid": "rid-1"}
    assert request_log.idempotency_key_actual() == ""


def test_la_marca_de_origen_ida_y_vuelta(monkeypatch) -> None:
    monkeypatch.setenv("MCP_ORIGIN_SECRET", "s")
    marca = identidad.firmar_origen("mcp", tenant="t", authorization="Bearer a")[
        identidad.CABECERA_ORIGEN
    ]

    assert identidad.verificar_origen(marca, tenant="t", authorization="Bearer a") == "mcp"
    assert identidad.verificar_origen(marca, tenant="t", authorization="Bearer b") is None
    assert identidad.verificar_origen("mcp.1.abc", tenant="t", authorization="Bearer a") is None
    assert identidad.verificar_origen(None, tenant="t", authorization="Bearer a") is None


def test_una_marca_vencida_o_del_futuro_no_vale(monkeypatch) -> None:
    import time

    monkeypatch.setenv("MCP_ORIGIN_SECRET", "s")
    ahora = time.time()
    for desfase in (-(identidad.VENTANA_ORIGEN_S + 5), identidad.VENTANA_ORIGEN_S + 5):
        marca = identidad.firmar_origen(
            "mcp", tenant="t", authorization="Bearer a", ahora=ahora + desfase
        )[identidad.CABECERA_ORIGEN]
        assert identidad.verificar_origen(marca, tenant="t", authorization="Bearer a") is None


def test_ip_real_firmada_exige_secreto_y_firma(monkeypatch) -> None:
    base = {
        "X-User-Id": "u",
        "X-Username": "n",
        "X-Tenant-Slug": "t",
        "X-User-Roles": "",
        "X-Canal": "api",
        "X-Real-Ip": "198.51.100.9",
    }
    monkeypatch.delenv("MCP_ORIGIN_SECRET", raising=False)
    assert identidad.ip_real_firmada(base) is None  # sin secreto: no se confía en nada

    monkeypatch.setenv("MCP_ORIGIN_SECRET", "s")
    firmadas = identidad.firmar_ip_real(identidad.firmar(base, metodo="GET", ruta="/api/v1/x"))
    assert identidad.ip_real_firmada(firmadas) == "198.51.100.9"
    assert identidad.ip_real_firmada({**firmadas, "X-Real-Ip": "6.6.6.6"}) is None
    assert identidad.ip_real_firmada(base) is None  # sin firma


# -- Secreto de origen SEPARADO del maestro de identidad (AREA-11-04/08) ---------


def test_el_secreto_maestro_no_sirve_para_marcar_el_canal(monkeypatch) -> None:
    """Quien solo tiene IDENTITY_SIGNING_SECRET no puede emitir una marca válida."""
    monkeypatch.delenv("MCP_ORIGIN_SECRET", raising=False)
    monkeypatch.setenv("IDENTITY_SIGNING_SECRET", "maestro")

    assert identidad.firmar_origen("mcp", tenant="t", authorization="Bearer a") == {}

    # Y una marca fabricada a mano con el maestro no la acepta quien verifica.
    monkeypatch.setenv("MCP_ORIGIN_SECRET", "origen")
    falsa = identidad._firma_origen("mcp", "1", "t", "Bearer a", "maestro")
    assert (
        identidad.verificar_origen(
            f"mcp.{int(__import__('time').time())}.{falsa}",
            tenant="t",
            authorization="Bearer a",
        )
        is None
    )


def test_el_secreto_de_origen_no_sirve_para_firmar_la_identidad(monkeypatch) -> None:
    """Quien solo tiene MCP_ORIGIN_SECRET no puede suplantar a una persona."""
    monkeypatch.delenv("IDENTITY_SIGNING_SECRET", raising=False)
    monkeypatch.setenv("MCP_ORIGIN_SECRET", "origen")
    cab = {"X-User-Id": "u", "X-Tenant-Slug": "t", "X-Real-Ip": "198.51.100.9"}

    assert identidad.CABECERA_FIRMA not in identidad.firmar(cab, metodo="GET", ruta="/x")

    monkeypatch.setenv("IDENTITY_SIGNING_SECRET", "maestro")
    forjada = {
        **cab,
        identidad.CABECERA_FIRMA: identidad._firma(cab, "origen", metodo="GET", ruta="/x"),
    }
    with pytest.raises(identidad.IdentidadNoAutentica):
        identidad.verificar(forjada, metodo="GET", ruta="/x")


def test_la_firma_de_la_ip_va_ligada_a_la_persona_y_la_entidad(monkeypatch) -> None:
    monkeypatch.setenv("MCP_ORIGIN_SECRET", "origen")
    monkeypatch.delenv("IDENTITY_SIGNING_SECRET", raising=False)
    cab = identidad.firmar_ip_real(
        {"X-User-Id": "u1", "X-Tenant-Slug": "t", "X-Real-Ip": "198.51.100.9"}
    )

    assert identidad.ip_real_firmada(cab) == "198.51.100.9"
    assert identidad.ip_real_firmada({**cab, "X-User-Id": "u2"}) is None
    assert identidad.ip_real_firmada({**cab, "X-Tenant-Slug": "otra"}) is None
    assert identidad.ip_real_firmada({**cab, "X-Real-Ip": "6.6.6.6"}) is None
