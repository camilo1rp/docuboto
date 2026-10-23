# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Índice del art. 20 con la entidad SUSPENDIDA (ADR-035/039, cierre del lote C).

`GET /api/v1/internal/documentos/clasificados` es la única lectura que se admite con la
entidad suspendida: el índice público se sirve en suspensión y archive-service lo compone
con ella. Mutaciones: quitar `admitir_conservacion` de `get_tenant_conn` (cae la primera);
admitir sin token (cae `test_sin_token...`); admitir `inactive` (cae `test_inactiva...`);
añadir una ruta a la lista sin querer (cae `test_otra_ruta...`)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core import conservacion
from app.core import database as db
from tests.test_estado_entidad import _Conn, _fila, _Pool, _request

_TOKEN = "tok-clasificados"
_RUTA = "/api/v1/internal/documentos/clasificados"


@pytest.fixture(autouse=True)
def _mundo(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    monkeypatch.setattr(conservacion.settings, "internal_service_token", _TOKEN)
    asiento = AsyncMock()
    monkeypatch.setattr(conservacion.audit, "append", asiento)
    conservacion._YA_ASENTADAS.clear()
    return asiento


async def _abrir(conn: _Conn, *, metodo: str = "GET", ruta: str = _RUTA, token: str | None = _TOKEN):
    pool = _Pool(conn)
    cab = {"X-Internal-Token": token} if token is not None else {}
    gen = db.get_tenant_conn(_request(pool, metodo=metodo, ruta=ruta, cabeceras=cab))  # type: ignore[arg-type]
    return gen, await gen.__anext__()


async def test_suspendida_con_token_sirve_los_clasificados_y_deja_asiento(_mundo: AsyncMock) -> None:
    conn = _Conn(_fila(status="suspended"))
    gen, entregada = await _abrir(conn)
    assert entregada is conn
    assert conn.ejecutados == ['SET search_path = "tenant_demo"']
    kw = _mundo.await_args.kwargs
    assert kw["action"] == "entidad.conservacion_admitida"
    assert kw["payload"] == {"estado_entidad": "suspended", "metodo": "GET", "ruta": _RUTA}
    await gen.aclose()


async def test_sin_token_sigue_vetada(_mundo: AsyncMock) -> None:
    with pytest.raises(HTTPException) as e:
        await _abrir(_Conn(_fila(status="suspended")), token=None)
    assert e.value.status_code == 403
    _mundo.assert_not_awaited()


async def test_inactiva_sigue_vetada_aunque_lleve_token(_mundo: AsyncMock) -> None:
    with pytest.raises(HTTPException) as e:
        await _abrir(_Conn(_fila(status="inactive")))
    assert e.value.status_code == 403
    _mundo.assert_not_awaited()


@pytest.mark.parametrize(
    ("metodo", "ruta"),
    [
        ("POST", _RUTA),
        ("GET", _RUTA + "/x"),
        ("POST", "/api/v1/internal/documentos/anexos"),
        ("GET", "/api/v1/documents/"),
    ],
)
async def test_otra_ruta_o_metodo_sigue_vetado(_mundo: AsyncMock, metodo: str, ruta: str) -> None:
    with pytest.raises(HTTPException) as e:
        await _abrir(_Conn(_fila(status="suspended")), metodo=metodo, ruta=ruta)
    assert e.value.status_code == 403
    _mundo.assert_not_awaited()
