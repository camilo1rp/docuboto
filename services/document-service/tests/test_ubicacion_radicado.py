# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""ADR-029 (unitario, mock): la ubicación actual del radicado en las lecturas.

La semántica de la columna generada, la migración y el no-read-up en el `COUNT`
contra el motor están en `tests/integration/test_ubicacion_radicado_integration.py`.
Aquí: el cableado (parámetro -> SQL de AMBAS consultas), la forma de la respuesta y
la frontera pública, que es un contrato de qué NO sale.
"""

from __future__ import annotations

import io
import json
import re
import uuid
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.document import DocumentResponse
from app.schemas.public import PublicVerifyResponse
from app.services.export_service import ExportService
from tests.conftest import TENANT_HEADERS, make_radicado_record
from tests.test_export import _row
from tests.test_search import _hit_row

# Lo que NO puede aparecer en nada que salga sin autenticar.
_CAMPOS_DE_UBICACION = ("ubicacion", "flujo_dept", "flujo_usuario")
_APP = Path(__file__).resolve().parents[1] / "app"


# --- el filtro -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_el_listado_filtra_por_ubicacion_en_el_select_y_en_el_count(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchval.return_value = 1
    conn.fetch.return_value = [make_radicado_record(flujo_dept="Control Interno", flujo_dept_code=500)]

    resp = await client.get("/api/v1/documents?ubicacion_dept_code=500", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    sql_count = conn.fetchval.call_args_list[-1].args[0]
    sql_select = conn.fetch.call_args_list[-1].args[0]
    for sql in (sql_count, sql_select):
        assert re.search(r"ubicacion_dept_code = \$\d+", sql), sql
    # Mismo valor ENLAZADO en ambas: el total cuenta exactamente lo que filtra la página.
    assert 500 in conn.fetchval.call_args_list[-1].args[1:]
    assert 500 in conn.fetch.call_args_list[-1].args[1:]
    cuerpo = resp.json()[0]
    assert (cuerpo["ubicacion_dept"], cuerpo["ubicacion_dept_code"]) == ("Control Interno", 500)
    assert cuerpo["dest_dept"] == "Dirección General"  # la de radicación sigue ahí, distinta


@pytest.mark.asyncio
async def test_el_listado_sin_filtro_no_menciona_la_ubicacion_en_el_where(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchval.return_value = 0
    conn.fetch.return_value = []

    await client.get("/api/v1/documents", headers=TENANT_HEADERS)

    assert "ubicacion_dept_code =" not in conn.fetchval.call_args_list[-1].args[0]


@pytest.mark.asyncio
async def test_el_listado_no_selecciona_el_poseedor(client_with_conn) -> None:
    """`flujo_usuario` es del detalle autenticado, no de las listas."""
    client, conn = client_with_conn
    conn.fetchval.return_value = 0
    conn.fetch.return_value = []

    await client.get("/api/v1/documents", headers=TENANT_HEADERS)

    assert "flujo_usuario" not in conn.fetch.call_args_list[-1].args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("valor", ["0", "abc", "1000000"])
async def test_el_codigo_de_ubicacion_se_valida(client_with_conn, valor: str) -> None:
    client, _ = client_with_conn
    resp = await client.get(f"/api/v1/documents?ubicacion_dept_code={valor}", headers=TENANT_HEADERS)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_la_busqueda_filtra_por_ubicacion_en_el_select_y_en_el_count(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchval.return_value = 1
    conn.fetch.return_value = [_hit_row(ubicacion_dept_code=500, ubicacion_dept="Control Interno")]

    resp = await client.get("/api/v1/search?ubicacion_dept_code=500", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    sql_count = conn.fetchval.call_args_list[0].args[0]
    sql_select = conn.fetch.call_args_list[0].args[0]
    for sql in (sql_count, sql_select):
        assert re.search(r"ubicacion_dept_code = \$\d+", sql), sql
    assert 500 in conn.fetchval.call_args_list[0].args[1:]
    hit = resp.json()["hits"][0]
    assert (hit["ubicacion_dept"], hit["ubicacion_dept_code"], hit["dest_dept"]) == (
        "Control Interno", 500, "Jurídica",
    )


# --- el detalle ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_el_detalle_lleva_el_poseedor_y_la_ubicacion(client_with_conn) -> None:
    client, conn = client_with_conn
    poseedor = uuid.uuid4()
    rec = make_radicado_record(flujo_dept="Control Interno", flujo_dept_code=500, flujo_usuario=poseedor)
    conn.fetchrow.return_value = rec
    conn.fetch.return_value = []

    resp = await client.get(f"/api/v1/documents/{rec['id']}", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    cuerpo = resp.json()
    assert cuerpo["flujo_usuario"] == str(poseedor)
    assert (cuerpo["ubicacion_dept"], cuerpo["ubicacion_dept_code"]) == ("Control Interno", 500)
    assert "flujo_usuario" in conn.fetchrow.call_args.args[0]


def test_la_respuesta_declara_la_ubicacion_con_default_nulo() -> None:
    campos = DocumentResponse.model_fields
    for nombre in ("flujo_dept", "flujo_dept_code", "flujo_usuario", "ubicacion_dept", "ubicacion_dept_code"):
        assert campos[nombre].default is None


# --- la frontera pública: un contrato de lo que NO sale ----------------------------


def test_la_verificacion_publica_no_expone_la_ubicacion() -> None:
    """Quien verifica un radicado sin autenticar ve la dependencia de RADICACIÓN (como
    siempre), nunca dónde está ahora ni quién lo tiene: ese dato es de trámite interno."""
    for nombre in PublicVerifyResponse.model_fields:
        assert not any(m in nombre for m in _CAMPOS_DE_UBICACION), nombre


@pytest.mark.parametrize(
    "ruta",
    ["schemas/public.py", "routers/public.py", "repositories/oai_repository.py",
     "routers/oai.py", "routers/cmis.py", "services/cmis_service.py", "services/oai_service.py"],
)
def test_ninguna_superficie_publica_menciona_la_ubicacion(ruta: str) -> None:
    # Sin `skip` por ausencia: un archivo renombrado dejaría el guardián mudo.
    texto = (_APP / ruta).read_text()
    for marca in _CAMPOS_DE_UBICACION:
        assert marca not in texto, f"{ruta} menciona {marca}: la frontera pública no la expone (ADR-029)"


# --- export / import -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_el_export_lleva_la_ubicacion_y_nunca_el_poseedor() -> None:
    row = _row() | {"ubicacion_dept": "Control Interno", "ubicacion_dept_code": 500}
    svc = ExportService(conn=None, tenant_slug="demo")
    repo = AsyncMock()
    repo.list_radicados.return_value = [row]
    repo.count_incluidos.return_value = 1
    repo.count_excluidos.return_value = 0
    repo.list_anexos.return_value = []
    repo.history.return_value = []
    svc._repo = repo
    with patch("app.services.export_service.audit.append", new=AsyncMock(return_value="h")):
        zbytes, *_ = await svc.build_export(max_clearance=1, comentario=None, year=None, doc_type=None)

    zf = zipfile.ZipFile(io.BytesIO(zbytes))
    entidad = json.loads(zf.read(f"radicados/{row['tracking_number']}.json"))
    assert entidad["metadata_sistema"]["ubicacion_dept"] == "Control Interno"
    assert entidad["metadata_sistema"]["ubicacion_dept_code"] == 500
    assert "flujo_usuario" not in json.dumps(entidad)
