# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La identidad firmada solo vale para el método y la ruta para los que se firmó (B10, ADR-036).

Una identidad legítima emitida para `proteger-indice` y reproducida contra
`legal-hold-internal` (otra operación del mismo servicio, con efectos distintos) tiene que
morir en el middleware con 401 `identidad_no_autentica`, antes de tocar ningún handler.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from orpycamcp_common import identidad

from app.main import app

SECRETO = "secreto-de-prueba-b10-ruta"
USER = "11111111-1111-1111-1111-111111111111"
INDICE = "/api/v1/preservacion/proteger-indice"
HOLD = "/api/v1/preservacion/legal-hold-internal/fijar"


@pytest.fixture(autouse=True)
def _secreto(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("IDENTITY_SIGNING_SECRET", SECRETO)
    # El middleware reclama el nonce en Redis (ADR-036, addendum C-7): en unidad, en memoria.
    identidad.fijar_registro(identidad.RegistroNoncesMemoria())
    yield
    identidad.fijar_registro(None)


async def _post(ruta: str, cabeceras: dict[str, str], metodo: str = "POST"):
    transporte = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transporte, base_url="http://test") as client:
        return await client.request(metodo, ruta, json={}, headers=cabeceras)


def _es_rechazo_de_identidad(r) -> bool:
    try:
        return r.status_code == 401 and r.json().get("error") == "identidad_no_autentica"
    except ValueError:
        return False


def _firmada(metodo: str, ruta: str) -> dict[str, str]:
    return identidad.firmar(
        {"X-Tenant-Slug": "demo", "X-User-Id": USER}, metodo=metodo, ruta=ruta
    )


@pytest.mark.asyncio
async def test_identidad_de_otra_ruta_da_401() -> None:
    r = await _post(HOLD, _firmada("POST", INDICE))
    assert _es_rechazo_de_identidad(r)


@pytest.mark.asyncio
async def test_identidad_de_otro_metodo_da_401() -> None:
    r = await _post(INDICE, _firmada("PUT", INDICE))
    assert _es_rechazo_de_identidad(r)


@pytest.mark.asyncio
async def test_control_positivo_la_misma_ruta_cruza_el_middleware() -> None:
    """Sin esto el rechazo de arriba podría ser un 401 por cualquier otra causa."""
    r = await _post(HOLD, _firmada("POST", HOLD))
    assert not _es_rechazo_de_identidad(r)


@pytest.mark.asyncio
async def test_el_middleware_verifica_con_el_metodo_de_la_peticion() -> None:
    """Una identidad firmada para GET cruza en un GET y no cruza si se presenta como POST."""
    ruta = "/api/v1/preservacion/estado"
    ok = await _post(ruta, _firmada("GET", ruta), metodo="GET")
    assert not _es_rechazo_de_identidad(ok)
    mal = await _post(ruta, _firmada("GET", ruta), metodo="POST")
    assert _es_rechazo_de_identidad(mal)
