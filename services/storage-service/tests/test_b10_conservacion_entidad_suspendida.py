# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Excepción de conservación para la entidad SUSPENDIDA (ADR-035, enmienda B10).

Entidad suspendida + ruta de la lista + `X-Internal-Token` => se atiende y deja asiento. Cualquier
otra combinación sigue vetada. Mutaciones: quitar `admitir_conservacion` de `get_db_conn` (cae
la primera); admitir sin token (cae `test_sin_token_...`); admitir sin mirar el estado, es
decir, aceptar también `inactive` (cae `test_una_entidad_inactiva_...`); añadir una ruta sin
lista (cae `test_una_ruta_no_listada_...`)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core import conservacion
from app.core.database import get_db_conn
from tests.test_estado_entidad_puerta import _Conn, _fila, _request

_TOKEN = "tok-conservacion"
_UUID = "11111111-1111-1111-1111-111111111111"
_RENOVAR = "/api/v1/preservacion/renovar-artefacto-internal"


@pytest.fixture(autouse=True)
def _mundo(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    monkeypatch.setattr(conservacion.settings, "internal_service_token", _TOKEN)
    asiento = AsyncMock()
    monkeypatch.setattr(conservacion.audit, "append", asiento)
    conservacion._YA_ASENTADAS.clear()
    return asiento


async def _abrir(conn: _Conn, *, metodo: str, ruta: str, token: str | None = _TOKEN):
    cab = {"X-Tenant-Slug": "demo"}
    if token is not None:
        cab["X-Internal-Token"] = token
    gen = get_db_conn(_request(conn, cab, metodo=metodo, ruta=ruta))  # type: ignore[arg-type]
    return gen, await gen.__anext__()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metodo", "ruta"),
    [
        ("POST", _RENOVAR),
        ("POST", "/api/v1/storage/internal/artefacto"),
        ("GET", f"/api/v1/storage/files/{_UUID}/download"),
        ("POST", "/api/v1/preservacion/renovar-retencion-internal"),
        ("POST", "/api/v1/preservacion/proteger-indice-internal"),
        ("POST", "/api/v1/preservacion/aip-internal"),
    ],
)
async def test_la_entidad_suspendida_conserva_por_las_rutas_listadas_y_deja_asiento(
    _mundo: AsyncMock, metodo: str, ruta: str
) -> None:
    conn = _Conn(_fila(status="suspended"))
    gen, entregada = await _abrir(conn, metodo=metodo, ruta=ruta)
    assert entregada is conn
    kw = _mundo.await_args.kwargs
    assert kw["action"] == "entidad.conservacion_admitida" and kw["tenant_slug"] == "demo"
    assert kw["payload"]["estado_entidad"] == "suspended" and _UUID not in kw["payload"]["ruta"]
    await gen.aclose()


@pytest.mark.asyncio
async def test_una_ruta_no_listada_sigue_vetada_aunque_lleve_token(_mundo: AsyncMock) -> None:
    conn = _Conn(_fila(status="suspended"))
    for metodo, ruta in [
        ("POST", "/api/v1/storage/upload"),
        ("POST", "/api/v1/preservacion/legal-hold-internal"),
        ("GET", _RENOVAR),  # método distinto
        ("POST", _RENOVAR + "/x"),  # prefijo, no coincidencia exacta
        ("POST", "/api/v1/storage/internal/purgar-huerfanos"),
    ]:
        with pytest.raises(HTTPException) as e:
            await _abrir(conn, metodo=metodo, ruta=ruta)
        assert e.value.status_code == 403, (metodo, ruta)
    _mundo.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, "otro"])
async def test_sin_token_o_con_token_ajeno_sigue_vetada(_mundo: AsyncMock, token: str | None) -> None:
    with pytest.raises(HTTPException) as e:
        await _abrir(_Conn(_fila(status="suspended")), metodo="POST", ruta=_RENOVAR, token=token)
    assert e.value.status_code == 403
    _mundo.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fila",
    [_fila(status="inactive"), None, _fila(status="suspended", prov="pending"), _fila(status="suspended", schema=False)],
    ids=["inactiva", "inexistente", "suspendida_sin_aprovisionar", "suspendida_sin_schema"],
)
async def test_una_entidad_inactiva_o_sin_schema_sigue_vetada(_mundo: AsyncMock, fila: dict | None) -> None:
    with pytest.raises(HTTPException) as e:
        await _abrir(_Conn(fila), metodo="POST", ruta=_RENOVAR)
    assert e.value.status_code in (403, 503)
    _mundo.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ruta",
    [
        f"/api/v1/storage/files/{_UUID}/download\n",  # `$` de re.match lo admitía
        f"/api/v1/storage/files/{_UUID}\n/download",
        f"/api/v1/storage/files/{_UUID}/download?x=1",
        _RENOVAR + "\n",
        _RENOVAR + "%0A",
        _RENOVAR + "%3Fa=b",
    ],
)
async def test_la_admision_es_por_coincidencia_exacta_del_scope(_mundo: AsyncMock, ruta: str) -> None:
    """N1. Mutación: `p.match` en vez de `p.fullmatch` -> cae el caso con `\\n` final."""
    with pytest.raises(HTTPException) as e:
        await _abrir(_Conn(_fila(status="suspended")), metodo="GET" if "download" in ruta else "POST", ruta=ruta)
    assert e.value.status_code == 403
    _mundo.assert_not_awaited()


@pytest.mark.asyncio
async def test_un_token_no_ascii_se_veta_sin_500(_mundo: AsyncMock) -> None:
    """N2. Mutación: `compare_digest` sobre `str` -> TypeError, no 403."""
    with pytest.raises(HTTPException) as e:
        await _abrir(_Conn(_fila(status="suspended")), metodo="POST", ruta=_RENOVAR, token="éééé")
    assert e.value.status_code == 403
    assert conservacion._token_valido("éééé") is False


@pytest.mark.asyncio
async def test_si_el_registro_falla_al_decidir_se_mantiene_el_veto(
    _mundo: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N3. Mutación: quitar el `try` -> la excepción sale (500) en vez de 403."""
    monkeypatch.setattr(conservacion, "atencion_publica", AsyncMock(side_effect=RuntimeError("caído")))
    with pytest.raises(HTTPException) as e:
        await _abrir(_Conn(_fila(status="suspended")), metodo="POST", ruta=_RENOVAR)
    assert e.value.status_code == 403
    _mundo.assert_not_awaited()


@pytest.mark.asyncio
async def test_el_asiento_es_uno_por_entidad_ruta_y_dia(_mundo: AsyncMock) -> None:
    """N4. Mutación: quitar el `if clave in _YA_ASENTADAS` -> 3 asientos."""
    conn = _Conn(_fila(status="suspended"))
    for _ in range(3):
        gen, _c = await _abrir(conn, metodo="POST", ruta=_RENOVAR)
        await gen.aclose()
    assert _mundo.await_count == 1
    # otra ruta normalizada = otro asiento; el mismo download con otro uuid = el mismo
    for u in (_UUID, "22222222-2222-2222-2222-222222222222"):
        gen, _c = await _abrir(conn, metodo="GET", ruta=f"/api/v1/storage/files/{u}/download")
        await gen.aclose()
    assert _mundo.await_count == 2
