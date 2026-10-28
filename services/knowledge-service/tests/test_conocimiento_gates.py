# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""ADR-037: las rutas de consulta exigen la bandera de la entidad Y PERM_CONOCIMIENTO.

Corren con los gates REALES (``gates_reales``) contra un ``conn`` simulado cuyo
``fetchval`` responde según la SQL: la bandera de ``public.tenants`` y el permiso del
usuario. Cada rama negativa tiene su control positivo (200) en la misma tabla.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tests.conftest import TENANT_HEADERS

pytestmark = [pytest.mark.gates_reales, pytest.mark.asyncio]

#: (método, ruta, cuerpo, estado cuando los gates dejan pasar). `/rag` pasa los gates
#: y responde 503: sin proveedor no hay generación (ADR-046).
RUTAS = [
    ("POST", "/api/v1/knowledge/search", {"query": "contratos"}, 200),
    ("POST", "/api/v1/knowledge/antecedentes", {"query": "contratos"}, 200),
    ("POST", "/api/v1/knowledge/rag", {"query": "contratos"}, 503),
    ("GET", "/api/v1/knowledge/availability", None, 200),
]
IDS = [r[1].rsplit("/", 1)[1] for r in RUTAS]


@pytest.fixture(autouse=True)
def _busqueda_vacia(monkeypatch: pytest.MonkeyPatch) -> None:
    """Estas pruebas miran los gates, no la búsqueda: document-service responde vacío."""
    from app.clients import document_search

    monkeypatch.setattr(document_search, "search", AsyncMock(return_value=[]))


def _fetchval(*, bandera: bool | Exception, crud: int):
    """``fetchval`` que distingue la lectura de la bandera de la del permiso."""

    async def _f(sql: str, *params: object) -> object:
        if "asistente_habilitado" in sql:
            if isinstance(bandera, Exception):
                raise bandera
            return bandera
        if "auth_group_permissions" in sql:  # check_permission(conn, user, perm, min_crud)
            _user, perm, min_crud = params
            assert perm == "PERM_CONOCIMIENTO"
            return crud >= min_crud
        return crud  # nivel_efectivo (mensaje del 403)

    return _f


async def _post(client, conn, ruta, *, bandera=True, crud=1, headers=None):
    conn.fetchval.side_effect = _fetchval(bandera=bandera, crud=crud)
    metodo, url, cuerpo, _ = ruta
    return await client.request(metodo, url, json=cuerpo, headers=headers or TENANT_HEADERS)


@pytest.mark.parametrize("ruta", RUTAS, ids=IDS)
async def test_con_bandera_y_permiso_la_consulta_pasa(client_with_conn, ruta) -> None:
    """Control positivo: bandera encendida + nivel 1 pasa los gates."""
    client, conn = client_with_conn
    resp = await _post(client, conn, ruta, bandera=True, crud=1)
    assert resp.status_code == ruta[3], resp.text


@pytest.mark.parametrize("ruta", RUTAS, ids=IDS)
async def test_sin_permiso_403_aunque_la_bandera_este_encendida(client_with_conn, ruta) -> None:
    client, conn = client_with_conn
    resp = await _post(client, conn, ruta, bandera=True, crud=0)
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.parametrize("ruta", RUTAS, ids=IDS)
async def test_bandera_apagada_403_aunque_tenga_el_permiso(client_with_conn, ruta) -> None:
    client, conn = client_with_conn
    resp = await _post(client, conn, ruta, bandera=False, crud=5)
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "asistente_deshabilitado"


async def test_bandera_apagada_tampoco_la_salva_ser_root(client_with_conn) -> None:
    """ROOT pasa `check_permission` por bypass; la bandera igual lo frena (va primero)."""
    client, conn = client_with_conn

    async def _f(sql: str, *params: object) -> object:
        return False if "asistente_habilitado" in sql else True  # permiso: siempre sí

    conn.fetchval.side_effect = _f
    resp = await client.post(RUTAS[0][1], json=RUTAS[0][2], headers=TENANT_HEADERS)
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "asistente_deshabilitado"


@pytest.mark.parametrize("ruta", RUTAS, ids=IDS)
async def test_bandera_ilegible_503_fail_closed(client_with_conn, ruta) -> None:
    client, conn = client_with_conn
    resp = await _post(client, conn, ruta, bandera=RuntimeError("registro caído"), crud=5)
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "asistente_estado_no_disponible"


@pytest.mark.parametrize("ruta", RUTAS, ids=IDS)
async def test_sin_identidad_401(client_with_conn, ruta) -> None:
    client, conn = client_with_conn
    sin_usuario = {k: v for k, v in TENANT_HEADERS.items() if k != "X-User-Id"}
    resp = await _post(client, conn, ruta, headers=sin_usuario)
    assert resp.status_code == 401
