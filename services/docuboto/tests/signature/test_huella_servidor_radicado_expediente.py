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
resuelve el SERVIDOR (SHA-256 del PDF vigente del radicado en document, del índice
electrónico vigente del expediente en archive), se fija al abrir la cadena y se
relee antes de cada firma. Aquí: la traducción de lo que dicen los productores
(`signature.core.huella`), el fail-closed (503 si el productor no responde) y el
gate (409 si el contenido cambió).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from archive import api as archive_api
from document import api as document_api
from signature.core import huella
from signature.core.huella import (
    BorradorDesaparecidoError,
    HuellaNoVerificableError,
    HuellaSinContenidoError,
    ObjetoDesaparecidoError,
)
from signature.main import app
from tests.signature.conftest import TENANT_HEADERS, USER
from tests.signature.test_cadena import _clear, _make_conn, _override, _sol_row

H = "f" * 64
_SECRETO = "SECRETO-interno traza db-7:5432 /app/signature/routers/x.py"


# ── lo que dicen los productores ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_la_huella_del_radicado_la_pide_a_document_con_su_referencia(monkeypatch) -> None:
    pedido = AsyncMock(return_value={"huella_sha256": H})
    monkeypatch.setattr(document_api, "radicado_fingerprint", pedido)
    datos = await huella.get_huella_radicado("2024-DEMO-E-000001", "demo")
    assert datos["huella_sha256"] == H
    assert pedido.await_args.kwargs == {"tenant_slug": "demo", "ref": "2024-DEMO-E-000001"}


@pytest.mark.asyncio
async def test_la_huella_del_expediente_la_pide_a_archive(monkeypatch) -> None:
    pedido = AsyncMock(return_value={"huella_sha256": H, "version": 2})
    monkeypatch.setattr(archive_api, "index_fingerprint", pedido)
    await huella.get_huella_indice_expediente("EXP-2024-0001", "demo")
    assert pedido.await_args.kwargs == {"tenant_slug": "demo", "ref": "EXP-2024-0001"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "excepcion"),
    [
        (document_api.DocumentNotFoundError("x"), ObjetoDesaparecidoError),
        (document_api.NothingToSealError("radicado_sin_documento", "x"), HuellaSinContenidoError),
        (document_api.DocumentUnavailableError(_SECRETO), HuellaNoVerificableError),
    ],
)
async def test_todo_lo_que_no_sea_una_huella_deniega(monkeypatch, error, excepcion) -> None:
    monkeypatch.setattr(document_api, "radicado_fingerprint", AsyncMock(side_effect=error))
    with pytest.raises(excepcion) as exc:
        await huella.get_huella_radicado("R", "demo")
    # El detalle del productor no viaja hasta el 503 que ve el firmante.
    assert "SECRETO" not in str(exc.value) and "db-7" not in str(exc.value)


@pytest.mark.asyncio
async def test_el_codigo_del_expediente_sin_indice_viaja_en_la_excepcion(monkeypatch) -> None:
    monkeypatch.setattr(
        archive_api,
        "index_fingerprint",
        AsyncMock(side_effect=archive_api.IndexNotGeneratedError("x")),
    )
    with pytest.raises(HuellaSinContenidoError) as exc:
        await huella.get_huella_indice_expediente("E", "demo")
    assert exc.value.codigo == "indice_no_disponible"
    # Subclase de «no verificable»: quien no la distinga cae al 503, nunca a «igual».
    assert isinstance(exc.value, HuellaNoVerificableError)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "excepcion"),
    [
        (archive_api.ExpedienteNotFoundError("x"), ObjetoDesaparecidoError),
        (archive_api.IndexUnavailableError(_SECRETO), HuellaNoVerificableError),
    ],
)
async def test_el_expediente_que_no_responde_o_no_existe_deniega(
    monkeypatch, error, excepcion
) -> None:
    monkeypatch.setattr(archive_api, "index_fingerprint", AsyncMock(side_effect=error))
    with pytest.raises(excepcion) as exc:
        await huella.get_huella_indice_expediente("E", "demo")
    assert "SECRETO" not in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "excepcion"),
    [
        (document_api.DraftNotFoundError("x"), BorradorDesaparecidoError),
        (document_api.DocumentUnavailableError(_SECRETO), HuellaNoVerificableError),
    ],
)
async def test_el_borrador_que_no_responde_o_desaparecio_deniega(
    monkeypatch, error, excepcion
) -> None:
    monkeypatch.setattr(document_api, "draft_fingerprint", AsyncMock(side_effect=error))
    with pytest.raises(excepcion) as exc:
        await huella.get_huella_borrador("b-1", "demo")
    assert "SECRETO" not in str(exc.value)


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
    from tests.signature.test_cadena import SOL_ID, _turno_row

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
    from tests.signature.test_cadena import SOL_ID, _turno_row

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


# ── el 503 no reenvía el detalle del productor (auditoría 2026-10-08) ───────


@pytest.mark.asyncio
async def test_el_503_que_ve_el_firmante_no_lleva_el_cuerpo_del_productor(
    monkeypatch,
) -> None:
    """De punta a punta: productor caído -> traducción real -> respuesta al llamante."""
    monkeypatch.setattr(
        document_api,
        "radicado_fingerprint",
        AsyncMock(side_effect=document_api.DocumentUnavailableError(_SECRETO)),
    )
    monkeypatch.setattr(
        "signature.services.cadena_service.get_huella_radicado", huella.get_huella_radicado
    )
    _override(_conn_para_abrir())
    try:
        resp = await _abrir("radicado")
    finally:
        _clear()
    assert resp.status_code == 503, resp.text
    assert "SECRETO" not in resp.text and "db-7" not in resp.text
    assert resp.json()["detail"]["error"] == "verificacion_documento_no_disponible"
    assert "document" in resp.json()["detail"]["detail"]
