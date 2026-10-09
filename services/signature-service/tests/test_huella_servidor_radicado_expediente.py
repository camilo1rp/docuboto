# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""
AREA-04-09 Media-2 — se firma el CONTENIDO, no el identificador.

Para `radicado` y `expediente` la huella ya no es el digest de `"{tipo}:{id}"`: la
resuelve el SERVIDOR (SHA-256 del PDF vigente del radicado en document-service,
del índice electrónico vigente del expediente en archive-service), se fija al abrir
la cadena y se relee antes de cada firma. Aquí: el contrato SALIENTE (la petición
lleva el token del servicio al que va), el fail-closed (503 si el productor no
responde) y el gate (409 si el contenido cambió).
"""

from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.core import http_clients
from app.core.config import settings
from app.core.http_clients import (
    HuellaNoVerificableError,
    HuellaSinContenidoError,
    ObjetoDesaparecidoError,
)
from app.main import app
from tests.conftest import TENANT_HEADERS, USER
from tests.test_cadena import _clear, _make_conn, _override, _sol_row

H = "f" * 64


def _doblar_http(monkeypatch, handler) -> list[httpx.Request]:
    """Sustituye el transporte de `httpx.AsyncClient` por `handler(request)`."""
    vistas: list[httpx.Request] = []
    real = httpx.AsyncClient

    def _handler(req: httpx.Request) -> httpx.Response:
        vistas.append(req)
        return handler(req)

    def _fabrica(**kw):  # noqa: ANN003
        return real(transport=httpx.MockTransport(_handler), **kw)

    monkeypatch.setattr(http_clients.httpx, "AsyncClient", _fabrica)
    return vistas


# ── contrato saliente ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_la_peticion_de_radicado_lleva_el_token_de_document_service(monkeypatch) -> None:
    monkeypatch.setattr(settings, "document_internal_token", "tok-document")
    monkeypatch.setattr(settings, "archive_internal_token", "tok-archive")
    vistas = _doblar_http(monkeypatch, lambda r: httpx.Response(200, json={"huella_sha256": H}))
    datos = await http_clients.get_huella_radicado("2024-DEMO-E-000001", "demo")
    assert datos["huella_sha256"] == H
    [req] = vistas
    assert req.method == "GET"
    assert req.url.host == "document-service"
    assert req.url.path == "/api/v1/internal/documentos/radicados/2024-DEMO-E-000001/huella"
    assert req.headers["X-Internal-Token"] == "tok-document"
    assert req.headers["X-Tenant-Slug"] == "demo"
    assert "authorization" not in req.headers  # content-free: no se propaga identidad


@pytest.mark.asyncio
async def test_la_peticion_de_expediente_lleva_el_token_de_archive_service(monkeypatch) -> None:
    monkeypatch.setattr(settings, "document_internal_token", "tok-document")
    monkeypatch.setattr(settings, "archive_internal_token", "tok-archive")
    vistas = _doblar_http(monkeypatch, lambda r: httpx.Response(200, json={"huella_sha256": H}))
    await http_clients.get_huella_indice_expediente("EXP-2024-0001", "demo")
    [req] = vistas
    assert req.url.host == "archive-service"
    assert req.url.path == "/api/v1/internal/expedientes/EXP-2024-0001/indice/huella"
    # El token del servicio AL QUE VA, nunca el del otro (mismo defecto que ya
    # rompió el circuito de aprobación: «cada llamada lleva el token del servicio
    # al que va»).
    assert req.headers["X-Internal-Token"] == "tok-archive"
    assert req.headers["X-Tenant-Slug"] == "demo"


@pytest.mark.asyncio
async def test_la_referencia_se_escapa_en_la_ruta(monkeypatch) -> None:
    vistas = _doblar_http(monkeypatch, lambda r: httpx.Response(200, json={"huella_sha256": H}))
    await http_clients.get_huella_radicado("a/b?c#d", "demo")
    assert "/radicados/a%2Fb%3Fc%23d/huella" in str(vistas[0].url)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("respuesta", "excepcion"),
    [
        (httpx.Response(404, json={}), ObjetoDesaparecidoError),
        (
            httpx.Response(409, json={"detail": {"error": "radicado_sin_documento"}}),
            HuellaSinContenidoError,
        ),
        (httpx.Response(401, json={}), HuellaNoVerificableError),
        (httpx.Response(403, json={}), HuellaNoVerificableError),
        (httpx.Response(503, json={}), HuellaNoVerificableError),
        (httpx.Response(200, content=b"no es json"), HuellaNoVerificableError),
        (httpx.Response(200, json=["no", "es", "objeto"]), HuellaNoVerificableError),
    ],
)
async def test_todo_lo_que_no_sea_un_200_legible_deniega(monkeypatch, respuesta, excepcion) -> None:
    _doblar_http(monkeypatch, lambda r: respuesta)
    with pytest.raises(excepcion):
        await http_clients.get_huella_radicado("R", "demo")


@pytest.mark.asyncio
async def test_el_codigo_del_409_viaja_en_la_excepcion(monkeypatch) -> None:
    _doblar_http(
        monkeypatch,
        lambda r: httpx.Response(409, json={"detail": {"error": "indice_no_disponible"}}),
    )
    with pytest.raises(HuellaSinContenidoError) as exc:
        await http_clients.get_huella_indice_expediente("E", "demo")
    assert exc.value.codigo == "indice_no_disponible"
    # Subclase de «no verificable»: quien no la distinga cae al 503, nunca a «igual».
    assert isinstance(exc.value, HuellaNoVerificableError)


@pytest.mark.asyncio
async def test_productor_inalcanzable_deniega(monkeypatch) -> None:
    def _cae(req):  # noqa: ANN001
        raise httpx.ConnectError("sin red", request=req)

    _doblar_http(monkeypatch, _cae)
    with pytest.raises(HuellaNoVerificableError):
        await http_clients.get_huella_indice_expediente("E", "demo")


# ── abrir la cadena ─────────────────────────────────────────────────────────


async def _abrir(tipo: str) -> httpx.Response:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        return await ac.post(
            "/api/v1/signature/requests",
            json={"objeto_tipo": tipo, "objeto_id": "X-1", "firmantes": [str(USER)]},
            headers=TENANT_HEADERS,
        )


def _conn_para_abrir():
    conn = _make_conn()
    conn.fetchval.side_effect = [True, 1, None, None, None]
    conn.fetchrow.side_effect = [{}, _sol_row()]
    conn.fetch.return_value = []
    return conn


@pytest.mark.asyncio
@pytest.mark.parametrize("tipo", ["radicado", "expediente"])
async def test_abrir_la_cadena_sin_poder_resolver_la_huella_es_503(
    tipo: str, _productores_de_huella_por_defecto
) -> None:
    """Una cadena con la huella equivocada es peor que ninguna: parece que protege."""
    _productores_de_huella_por_defecto[tipo].side_effect = HuellaNoVerificableError("caído")
    _override(_conn_para_abrir())
    try:
        resp = await _abrir(tipo)
    finally:
        _clear()
    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"]["error"] == "verificacion_documento_no_disponible"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tipo", "codigo"),
    [("radicado", "radicado_sin_documento"), ("expediente", "indice_no_disponible")],
)
async def test_abrir_la_cadena_sin_contenido_es_409_con_el_codigo_del_productor(
    tipo: str, codigo: str, _productores_de_huella_por_defecto
) -> None:
    _productores_de_huella_por_defecto[tipo].side_effect = HuellaSinContenidoError(codigo, "x")
    _override(_conn_para_abrir())
    try:
        resp = await _abrir(tipo)
    finally:
        _clear()
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == codigo


@pytest.mark.asyncio
async def test_abrir_la_cadena_sobre_un_objeto_desaparecido_es_el_404_neutro(
    _productores_de_huella_por_defecto,
) -> None:
    _productores_de_huella_por_defecto["radicado"].side_effect = ObjetoDesaparecidoError("no")
    _override(_conn_para_abrir())
    try:
        resp = await _abrir("radicado")
    finally:
        _clear()
    assert resp.status_code == 404, resp.text


# ── firmar: el contenido cambió ─────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("tipo", ["radicado", "expediente"])
async def test_firmar_con_el_contenido_cambiado_es_409_documento_modificado(
    tipo: str, _productores_de_huella_por_defecto
) -> None:
    from tests.test_cadena import SOL_ID, _turno_row

    _productores_de_huella_por_defecto[tipo].return_value = {"huella_sha256": "b" * 64, "version": 9}
    conn = _make_conn()
    sol = {**_sol_row(), "objeto_tipo": tipo}  # payload_sha256 = "a"*64 al abrir
    turno = _turno_row(firmante_id=USER)
    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [sol, {}, turno]
    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={},
                headers={**TENANT_HEADERS, "Authorization": "Bearer x"},
            )
    finally:
        _clear()
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "documento_modificado"


@pytest.mark.asyncio
@pytest.mark.parametrize("tipo", ["radicado", "expediente"])
async def test_firmar_sin_poder_consultar_al_productor_es_503(
    tipo: str, _productores_de_huella_por_defecto
) -> None:
    from tests.test_cadena import SOL_ID, _turno_row

    _productores_de_huella_por_defecto[tipo].side_effect = HuellaNoVerificableError("caído")
    conn = _make_conn()
    sol = {**_sol_row(), "objeto_tipo": tipo}
    turno = _turno_row(firmante_id=USER)
    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [sol, {}, turno]
    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                json={},
                headers={**TENANT_HEADERS, "Authorization": "Bearer x"},
            )
    finally:
        _clear()
    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"]["error"] == "verificacion_documento_no_disponible"


# ── el 503 no reenvía el cuerpo del productor (auditoría 2026-10-08) ─────────


_SECRETO = "SECRETO-interno traza db-7:5432 /app/app/routers/x.py"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "respuesta",
    [
        httpx.Response(500, text=_SECRETO),
        httpx.Response(502, text=_SECRETO),
        httpx.Response(409, text=_SECRETO),  # 409 sin JSON de error: cuerpo ilegible
    ],
)
async def test_el_mensaje_de_la_excepcion_no_lleva_el_cuerpo_del_productor(
    monkeypatch, respuesta
) -> None:
    _doblar_http(monkeypatch, lambda r: respuesta)
    with pytest.raises(HuellaNoVerificableError) as exc:
        await http_clients.get_huella_radicado("R", "demo")
    assert "SECRETO" not in str(exc.value) and "db-7" not in str(exc.value)
    assert "document-service" in str(exc.value)


@pytest.mark.asyncio
async def test_el_mensaje_no_lleva_el_host_interno_de_httpx(monkeypatch) -> None:
    def _cae(req):  # noqa: ANN001
        raise httpx.ConnectError("[Errno -2] Name or service not known db-7.internal:5432", request=req)

    _doblar_http(monkeypatch, _cae)
    with pytest.raises(HuellaNoVerificableError) as exc:
        await http_clients.get_huella_indice_expediente("E", "demo")
    assert "db-7" not in str(exc.value) and "5432" not in str(exc.value)
    assert str(exc.value) == "archive-service inalcanzable"


@pytest.mark.asyncio
async def test_el_cuerpo_del_borrador_tampoco_se_reenvia(monkeypatch) -> None:
    _doblar_http(monkeypatch, lambda r: httpx.Response(500, text=_SECRETO))
    with pytest.raises(HuellaNoVerificableError) as exc:
        await http_clients.get_huella_borrador("b-1", "demo")
    assert "SECRETO" not in str(exc.value)


@pytest.mark.asyncio
async def test_el_409_de_un_cuerpo_ilegible_no_lo_reenvia(monkeypatch) -> None:
    _doblar_http(monkeypatch, lambda r: httpx.Response(409, text=_SECRETO))
    with pytest.raises(HuellaSinContenidoError) as exc:
        await http_clients.get_huella_radicado("R", "demo")
    assert "SECRETO" not in str(exc.value)
    assert exc.value.codigo == "objeto_sin_contenido"


@pytest.mark.asyncio
async def test_el_503_que_ve_el_firmante_no_lleva_el_cuerpo_del_productor(
    monkeypatch,
) -> None:
    """De punta a punta: cliente real -> servicio -> respuesta HTTP al llamante."""
    _doblar_http(monkeypatch, lambda r: httpx.Response(500, text=_SECRETO))
    monkeypatch.setattr(
        "app.services.cadena_service.get_huella_radicado", http_clients.get_huella_radicado
    )
    _override(_conn_para_abrir())
    try:
        resp = await _abrir("radicado")
    finally:
        _clear()
    assert resp.status_code == 503, resp.text
    assert "SECRETO" not in resp.text and "db-7" not in resp.text
    assert resp.json()["detail"]["error"] == "verificacion_documento_no_disponible"
    assert "document-service" in resp.json()["detail"]["detail"]
