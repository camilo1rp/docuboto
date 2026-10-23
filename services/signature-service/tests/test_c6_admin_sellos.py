# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""C-6: `GET /api/v1/signature/admin/sellos-archivo` (unit, con doble de conexión).

El SQL (clasificación, orden, recuento == filas, acotado por clearance) se ejerce contra Postgres real
en `tests/integration/test_c6_admin_sellos_sql.py`; aquí el contrato HTTP: gate, parámetros, cabeceras,
asiento de la consulta y el guardián de los productores de eslabones.
"""

from __future__ import annotations

import ast
import inspect
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.repositories.firma_repository import SITUACIONES_SELLO, FirmaRepository
from tests.conftest import TENANT_HEADERS

BASE = "/api/v1/signature/admin/sellos-archivo"
AHORA = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def _fila(**kw) -> dict:  # noqa: ANN003
    base = {
        "firma_id": uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001"),
        "ordinal": 2, "estado": "vigente", "situaciones": ["por_vencer", "sin_revocacion"],
        "gen_time": AHORA - timedelta(days=300), "vence_at": AHORA + timedelta(days=72),
        "dias_restantes": 72, "tsa_cert_fingerprint": "ab" * 32, "tsa_revocacion_fuente": "ninguna",
        "revocacion_tsa_previa": "crl", "ultimo_aviso": "sin_revocacion_tsa",
        "objeto_tipo": "indice", "objeto_id": "IDX-1", "objeto_reservado": False,
    }
    return {**base, **kw}


@pytest.fixture
def _clearance():  # noqa: ANN202
    with patch("app.routers.admin_sellos.resolve_caller_clearance", AsyncMock(return_value=1)) as m:
        yield m


@pytest.fixture
def _asiento():  # noqa: ANN202
    with patch("app.routers.admin_sellos._audit.append", AsyncMock(return_value="h")) as m:
        yield m


@pytest.mark.asyncio
async def test_exige_administracion_a_nivel_3(client_with_conn) -> None:  # noqa: ANN001
    """El gate es `USUA_PERM_ADMIN` con `min_crud=3`: el nivel 2 NO alcanza. Mutación: bajar el
    `min_crud` en el router -> cae."""
    client, conn = client_with_conn
    with patch("app.core.clearance.check_permission", AsyncMock(return_value=True)) as cp:
        conn.fetch.return_value = []
        conn.fetchval.return_value = 0
        resp = await client.get(BASE, headers=TENANT_HEADERS)
    assert resp.status_code == 200
    args = cp.await_args.args
    assert args[2] == "USUA_PERM_ADMIN" and args[3] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("ruta", ["", "/resumen"])
async def test_sin_permiso_es_403_y_no_se_consulta_nada(client_with_conn, ruta: str) -> None:  # noqa: ANN001
    client, conn = client_with_conn
    conn.fetchval.return_value = None  # check_permission -> denegado
    resp = await client.get(BASE + ruta, headers=TENANT_HEADERS)
    assert resp.status_code == 403
    conn.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_lista_con_total_y_campos_content_free(client_with_conn, _clearance, _asiento) -> None:  # noqa: ANN001
    client, conn = client_with_conn
    conn.fetch.side_effect = [[{"table_name": "radicados"}, {"table_name": "expedientes"}], [_fila()]]
    conn.fetchval.side_effect = [True, 7]  # permiso, total
    resp = await client.get(f"{BASE}?situacion=por_vencer&situacion=sin_revocacion&size=1", headers=TENANT_HEADERS)
    assert resp.status_code == 200, resp.text
    assert resp.headers["X-Total-Count"] == "7"
    (item,) = resp.json()
    assert item["firma_id"] == "aaaaaaaa-0000-0000-0000-000000000001"
    assert item["situaciones"] == ["por_vencer", "sin_revocacion"] and item["dias_restantes"] == 72
    assert item["objeto_reservado"] is False and item["objeto_tipo"] == "indice"
    # content-free: ni asunto ni XML ni clave de storage
    assert not {"asunto", "minio_key", "payload_xml", "xml"} & set(item)


@pytest.mark.asyncio
async def test_objeto_reservado_va_acotado_pero_la_fila_se_ve(client_with_conn, _clearance, _asiento) -> None:  # noqa: ANN001
    client, conn = client_with_conn
    conn.fetch.side_effect = [[], [_fila(objeto_tipo=None, objeto_id=None, objeto_reservado=True)]]
    conn.fetchval.side_effect = [True, 1]
    resp = await client.get(BASE, headers=TENANT_HEADERS)
    (item,) = resp.json()
    assert item["objeto_reservado"] is True and item["objeto_tipo"] is None and item["objeto_id"] is None
    assert item["firma_id"], "la fila SIEMPRE se ve: ocultarla escondería una pérdida de prueba"


@pytest.mark.asyncio
async def test_la_consulta_deja_un_asiento_por_peticion(client_with_conn, _clearance, _asiento) -> None:  # noqa: ANN001
    client, conn = client_with_conn
    conn.fetch.side_effect = [[], [_fila()]]
    conn.fetchval.side_effect = [True, 1]
    await client.get(f"{BASE}?situacion=vencido&dias=30&page=2&size=5", headers=TENANT_HEADERS)
    _asiento.assert_awaited_once()
    kw = _asiento.await_args.kwargs
    assert kw["action"] == "firma.sellos_archivo_consultados" and kw["service"] == "signature-service"
    assert kw["actor"] == TENANT_HEADERS["X-User-Id"]
    assert kw["payload"]["situaciones"] == ["vencido"] and kw["payload"]["dias"] == 30
    assert kw["payload"]["page"] == 2 and kw["payload"]["total"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    ["size=101", "size=0", "page=0", "dias=0", "dias=3651", "situacion=inventada", "situacion=vencido&situacion=x"],
)
async def test_parametros_fuera_de_rango_son_422_sin_consultar(client_with_conn, _clearance, query: str) -> None:  # noqa: ANN001
    """`size` nunca pasa del `le=100` (lección «el tope confundido con holgura»)."""
    client, conn = client_with_conn
    resp = await client.get(f"{BASE}?{query}", headers=TENANT_HEADERS)
    assert resp.status_code == 422
    conn.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_el_margen_por_omision_es_el_de_la_renovacion(client_with_conn, _clearance, _asiento) -> None:  # noqa: ANN001
    from app.core.config import settings

    client, conn = client_with_conn
    conn.fetch.side_effect = [[], []]
    conn.fetchval.side_effect = [True, 0]
    await client.get(BASE, headers=TENANT_HEADERS)
    assert _asiento.await_args.kwargs["payload"]["dias"] == settings.lta_renovacion_margen_dias


@pytest.mark.asyncio
async def test_resumen_completa_con_ceros_y_lleva_el_margen(client_with_conn, _asiento) -> None:  # noqa: ANN001
    client, conn = client_with_conn
    conn.fetch.return_value = [{"sit": "vencido", "n": 2}, {"sit": "por_vencer", "n": 5}]
    resp = await client.get(f"{BASE}/resumen?dias=45", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == {
        "por_vencer": 5, "vencido": 2, "sin_revocacion": 0, "pendiente_worm": 0, "rechazado": 0,
        "margen_dias": 45,
    }
    _asiento.assert_awaited_once()


# ---------------------------------------------------------------------------
# Guardián: los productores de eslabones escriben las tres columnas de C-6
# ---------------------------------------------------------------------------


def test_insert_sello_archivo_no_tiene_valor_por_omision_en_las_columnas_de_c6() -> None:
    """Un NULL explícito ignora el DEFAULT: si el parámetro tuviera uno, un productor podría omitirlo y
    dejar un eslabón que la consulta de administración no sabe clasificar."""
    params = inspect.signature(FirmaRepository.insert_sello_archivo).parameters
    for nombre in ("tsa_cadena_not_after", "tsa_revocacion_fuente", "revocacion_tsa_previa"):
        assert params[nombre].default is inspect.Parameter.empty, nombre


def test_todo_productor_de_eslabones_pasa_las_tres_columnas() -> None:
    """AST de `app/`: cada llamada a `insert_sello_archivo` nombra las tres. Un productor nuevo que las
    omita falla aquí y no en producción."""
    raiz = Path(__file__).resolve().parents[1] / "app"
    llamadas = 0
    for py in raiz.rglob("*.py"):
        for nodo in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if (
                isinstance(nodo, ast.Call)
                and isinstance(nodo.func, ast.Attribute)
                and nodo.func.attr == "insert_sello_archivo"
            ):
                llamadas += 1
                nombres = {k.arg for k in nodo.keywords}
                faltan = {"tsa_cadena_not_after", "tsa_revocacion_fuente", "revocacion_tsa_previa"} - nombres
                assert not faltan, f"{py.name}:{nodo.lineno} omite {sorted(faltan)}"
    assert llamadas >= 2, "esperaba los dos productores: signing_service (ordinal 0) y renovacion_lta"


def test_las_situaciones_del_contrato() -> None:
    assert SITUACIONES_SELLO == ("por_vencer", "vencido", "sin_revocacion", "pendiente_worm", "rechazado")
