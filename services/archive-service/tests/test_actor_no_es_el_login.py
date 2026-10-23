# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Quien hizo el acto es su UUID, no su login (AREA-02-08).

`app/core/actor.py` ya declaraba que `X-Username` es solo un atributo
descriptivo («nunca como identidad»), pero dos sitios lo seguían asignando como
actor: `fisico._performed_by` —quién prestó o devolvió una unidad física— e
`indice.rebuild_indice` —el `generado_por` del índice electrónico, que luego se
firma—. Keycloak permite renombrar a un usuario aunque `editUsernameAllowed`
sea false, así que la fila «lo hizo juan» deja de ser reconstruible el día que
juan se llama otra cosa.

Con `X-User-Id=U` y `X-Username=juan`, lo que se guarda es `U`.
"""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import (
    EXPEDIENTE_ID,
    NOW,
    TENANT_HEADERS,
    USER_ID,
    make_expediente_record,
    make_radicado_link_record,
)
from tests.test_fisico import _prestamo_row, _unidad_row
from tests.test_indice import _expected_rebuild_hash, _indice_row

_LOGIN = {"X-Username": "juan"}


def _performed_by_de_los_movimientos(mock_conn) -> list[str | None]:
    return [
        c.args[5]
        for c in mock_conn.execute.call_args_list
        if c.args
        and isinstance(c.args[0], str)
        and "INSERT INTO movimiento_fisico" in c.args[0]
    ]


@pytest.mark.asyncio
async def test_el_prestamo_guarda_el_uuid_y_no_el_login(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    unidad_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _unidad_row(id=unidad_id),
        None,  # resolve_caller_clearance -> PUBLICA
        _prestamo_row(unidad_id=unidad_id),
    ]

    resp = await client.post(
        f"/api/v1/unidades/{unidad_id}/prestamos",
        json={
            "prestatario": {
                "tipo": "externo",
                "nombre": "Ana Jurídica",
                "documento_tipo": "CC",
                "documento_numero": "1234567890",
            },
            "fecha_devolucion_esperada": "2099-01-01",
        },
        headers={**TENANT_HEADERS, **_LOGIN},
    )

    assert resp.status_code == 201
    assert _performed_by_de_los_movimientos(mock_conn) == [str(USER_ID)]


@pytest.mark.asyncio
async def test_la_devolucion_guarda_el_uuid_y_no_el_login(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    pid = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _prestamo_row(id=pid),
        _prestamo_row(id=pid, estado="devuelto", fecha_devolucion_real=NOW),
    ]

    resp = await client.post(
        f"/api/v1/prestamos/{pid}/devolver", headers={**TENANT_HEADERS, **_LOGIN}
    )

    assert resp.status_code == 200
    assert _performed_by_de_los_movimientos(mock_conn) == [str(USER_ID)]


@pytest.mark.asyncio
async def test_el_indice_regenerado_guarda_el_uuid_como_generado_por(
    client_with_conn,
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    rad_id = uuid.uuid4()
    exp = make_expediente_record(exp_id=EXPEDIENTE_ID)
    link_viejo = make_radicado_link_record(radicado_id=rad_id, content_hash="9" * 64, added_at=NOW)
    anterior = await _expected_rebuild_hash(exp, [link_viejo])
    mock_conn.fetchrow.side_effect = [
        exp,
        _indice_row(version=3, content=anterior),
        _indice_row(version=4),
        None,
        exp,
    ]
    # Cambia el contenido (folios declarados) para que se genere una versión nueva.
    mock_conn.fetch.return_value = [
        make_radicado_link_record(
            radicado_id=rad_id, content_hash="9" * 64, folios=42, added_at=NOW
        )
    ]

    resp = await client.post(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/indice/rebuild",
        headers={**TENANT_HEADERS, **_LOGIN},
    )

    assert resp.status_code == 200 and resp.json()["unchanged"] is False
    insert = next(
        c.args
        for c in mock_conn.fetchrow.await_args_list
        if c.args and isinstance(c.args[0], str) and "INSERT INTO expediente_indice" in c.args[0]
    )
    # (sql, expediente, version, xml, sha, content_hash, num_docs, generado_por, xsd)
    assert insert[7] == str(USER_ID)
    assert "juan" not in insert
