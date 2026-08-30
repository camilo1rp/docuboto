# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Quién leyó y quién descargó — historial de acceso del expediente (RF-BUS-10;
Ley 594/2000 art. 22; Ley 1712/2014).

**El hallazgo que corrigen estos tests.** La traza existía y era buena:
`document.radicado_consultado` y `document.anexo_descargado`, con actor, canal y
nivel de seguridad, y descartando el canal `system` y el actor nulo. Pero se
escribe con `object_type='radicado'` y `object_ref=<número de radicado>`,
mientras el historial del expediente filtraba por el **código del expediente**.
Resultado: la pregunta «¿quién descargó los documentos de este expediente?» no
tenía respuesta en la pantalla hecha para responderla, **aunque el dato
estuviera guardado desde el principio**.

Lo que se prueba:

* Los números de radicado del expediente **entran en la consulta**.
* La lista de acciones es de **admisión** y solo de lecturas: un radicado
  acumula además creación, anulación y reclasificación, y volcarlas aquí
  convertiría el historial de acceso en un historial de todo.
* **El recuento y la página comparten la MISMA condición**: si vivieran
  separadas, el total podría contar entradas que la página no muestra.
"""


import pytest

from tests.conftest import (
    TENANT_HEADERS,
    make_expediente_record,
    make_radicado_link_record,
)

_CLEARANCE = {"is_root": False, "max_level": 3}


def _expediente_row(code="EXP-2026-0001"):
    """Fila del expediente construida con el constructor del `conftest`, no a
    mano: una fila inventada se desincroniza del mapeo real y el test acabaría
    midiendo el doble en vez del código."""
    return make_expediente_record(code=code)


def _vinculo(tracking):
    return make_radicado_link_record(tracking_number=tracking)


@pytest.mark.asyncio
async def test_los_radicados_del_expediente_entran_en_la_consulta(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    exp = _expediente_row()
    # Un solo valor: `fetchval` lo usan el gate de permiso y el recuento, y una
    # lista se agota en cuanto cambia el orden interno. `1` sirve a los dos.
    mock_conn.fetchval.return_value = 1
    mock_conn.fetchrow.side_effect = [_CLEARANCE, exp]
    mock_conn.fetch.side_effect = [
        [_vinculo("2026-DEMO-E-000001"), _vinculo("2026-DEMO-S-000002")],
        [],
    ]

    resp = await client.get(
        f"/api/v1/expedientes/{exp['id']}/historial-acceso", headers=TENANT_HEADERS
    )

    assert resp.status_code == 200
    args = mock_conn.fetchval.await_args.args
    trackings = args[4]
    assert set(trackings) == {"2026-DEMO-E-000001", "2026-DEMO-S-000002"}


@pytest.mark.asyncio
async def test_solo_las_acciones_de_ACCESO_del_radicado_no_todo_su_ciclo_de_vida(
    client_with_conn,
) -> None:
    client, mock_conn = client_with_conn
    exp = _expediente_row()
    # Un solo valor: `fetchval` lo usan el gate de permiso y el recuento, y una
    # lista se agota en cuanto cambia el orden interno. `1` sirve a los dos.
    mock_conn.fetchval.return_value = 1
    mock_conn.fetchrow.side_effect = [_CLEARANCE, exp]
    mock_conn.fetch.side_effect = [[_vinculo("2026-DEMO-E-000001")], []]

    await client.get(
        f"/api/v1/expedientes/{exp['id']}/historial-acceso", headers=TENANT_HEADERS
    )

    acciones = set(mock_conn.fetchval.await_args.args[5])
    assert acciones == {"document.radicado_consultado", "document.anexo_descargado"}
    # Ni la creación ni la anulación son accesos.
    assert "document.radicado_created" not in acciones


@pytest.mark.asyncio
async def test_el_recuento_y_la_pagina_comparten_la_MISMA_condicion(client_with_conn) -> None:
    """Si vivieran separadas, el total podría contar entradas que la página no
    muestra — o al revés — y nadie se enteraría."""
    client, mock_conn = client_with_conn
    exp = _expediente_row()
    # Un solo valor: `fetchval` lo usan el gate de permiso y el recuento, y una
    # lista se agota en cuanto cambia el orden interno. `1` sirve a los dos.
    mock_conn.fetchval.return_value = 1
    mock_conn.fetchrow.side_effect = [_CLEARANCE, exp]
    mock_conn.fetch.side_effect = [[_vinculo("2026-DEMO-E-000001")], []]

    await client.get(
        f"/api/v1/expedientes/{exp['id']}/historial-acceso", headers=TENANT_HEADERS
    )

    sql_count = mock_conn.fetchval.await_args.args[0]
    sql_page = mock_conn.fetch.await_args.args[0]
    condicion = "object_type = 'radicado'"
    assert condicion in sql_count
    assert condicion in sql_page
    # Y los mismos parámetros en el mismo orden.
    assert mock_conn.fetchval.await_args.args[1:] == mock_conn.fetch.await_args.args[1:-2]
