# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""AREA-06-07: dos juegos de campos de retención en `trd_series`. El cálculo real usa
`archivo_gestion_years`/`archivo_central_years`; `retention_years`/`total_retention` son el
juego legado. La respuesta DERIVA el legado de las fases (no devuelve la columna legada que
pudo quedar en desacuerdo) y un PATCH de las fases también reescribe el legado."""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import TENANT_HEADERS, make_trd_serie_record


@pytest.mark.asyncio
async def test_patch_de_archivo_gestion_deriva_el_total_de_las_dos_fases(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    serie_id = uuid.uuid4()
    existing = make_trd_serie_record(serie_id=serie_id, estado="borrador")  # 5 + 5, total 10
    # La fila que devuelve el UPDATE trae las FASES nuevas y el juego legado VIEJO: es
    # justo el desacuerdo que el mapper debe esconder derivando.
    devuelta = make_trd_serie_record(
        serie_id=serie_id,
        estado="borrador",
        retention_years=5,
        total_retention=10,
        archivo_gestion_years=7,
        archivo_central_years=5,
    )
    mock_conn.fetchrow.side_effect = [existing, devuelta]

    resp = await client.patch(
        f"/api/v1/trd/{serie_id}", json={"archivo_gestion_years": 7}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["archivo_gestion_years"] == 7 and cuerpo["archivo_central_years"] == 5
    assert cuerpo["total_retention"] == 12  # gestión + central, NO el 10 de la columna legada
    assert cuerpo["retention_years"] == 7
    # el UPDATE reescribe tambien el legado, derivado de las fases finales (7 y 7+5)
    update = next(c for c in mock_conn.fetchrow.call_args_list if "UPDATE trd_series" in c.args[0])
    assert "archivo_gestion_years" in update.args[0] and "total_retention" in update.args[0]
    assert 7 in update.args and 12 in update.args


@pytest.mark.asyncio
async def test_patch_de_las_fases_sobre_una_version_convalidada_es_inmutable(client_with_conn) -> None:
    """Las fases son tan sustantivas como el juego legado: el gate de inmutabilidad
    (409) también las cubre; si no, bastaba PATCHear `archivo_*` para saltárselo."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    serie_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [make_trd_serie_record(serie_id=serie_id, estado="convalidada")]

    resp = await client.patch(
        f"/api/v1/trd/{serie_id}", json={"archivo_central_years": 99}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "trd_version_inmutable"


@pytest.mark.asyncio
async def test_sin_las_dos_fases_se_devuelve_el_legado_tal_cual(client_with_conn) -> None:
    """Filas sin migrar (alguna fase NULL): no hay de dónde derivar y se conserva la columna."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    serie_id = uuid.uuid4()
    fila = make_trd_serie_record(
        serie_id=serie_id,
        retention_years=3,
        total_retention=9,
        archivo_gestion_years=None,
        archivo_central_years=None,
    )
    mock_conn.fetchrow.side_effect = [fila]

    resp = await client.get(f"/api/v1/trd/{serie_id}", headers=TENANT_HEADERS)

    assert resp.status_code == 200, resp.text
    assert (resp.json()["retention_years"], resp.json()["total_retention"]) == (3, 9)
