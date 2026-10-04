# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""AREA-07-14: `POST /expedientes/{id}/aip/reintentar` devuelve a `pendiente` un AIP
`agotado` (el job solo lo pasa a `agotado`; nunca volvía).

Mockeado: lo que se fija es la forma de la consulta (estado y clearance EN el WHERE, un
solo UPDATE), el nivel de permiso, el asiento y el 404 neutro. El efecto real contra
Postgres (un `empaquetado` no se toca) está en `tests/integration/test_aip_reintento_manual_sql.py`.
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.conftest import EXPEDIENTE_ID, TENANT_HEADERS, USER_ID

_URL = f"/api/v1/expedientes/{EXPEDIENTE_ID}/aip/reintentar"


def _fila(**kw) -> dict:
    base = {
        "expediente_id": EXPEDIENTE_ID,
        "estado": "pendiente",
        "intentos_previos": 8,
        "error_previo": "storage_unavailable",
    }
    base.update(kw)
    return base


def _audit(mock_conn) -> list[tuple]:
    return [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]


@pytest.mark.asyncio
async def test_reintento_de_un_aip_agotado_lo_devuelve_a_pendiente_con_asiento(
    client_with_conn,
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.side_effect = [True, "EXP-2024-0001", None]  # RBAC, code, prev_hash audit
    mock_conn.fetchrow.side_effect = [None, _fila()]  # caller_clearance (->1), UPDATE...RETURNING

    resp = await client.post(_URL, headers=TENANT_HEADERS)

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"expediente_id": str(EXPEDIENTE_ID), "estado": "pendiente"}
    # el estado y el clearance van EN el WHERE del UPDATE, no en una comprobación previa
    sql = mock_conn.fetchrow.await_args_list[-1].args[0]
    assert "ea.estado = 'agotado'" in sql
    assert "e.nivel_seguridad <= $2" in sql
    assert "UPDATE expediente_aip" in sql
    asientos = _audit(mock_conn)
    assert len(asientos) == 1 and asientos[0][5] == "archive.aip_reintento_manual"
    assert asientos[0][3] == str(USER_ID)
    payload = json.loads(asientos[0][8])
    assert payload["intentos_previos"] == 8 and payload["error_previo"] == "storage_unavailable"


@pytest.mark.asyncio
async def test_reintento_que_no_aplica_es_404_neutro_sin_asiento(client_with_conn) -> None:
    """AIP no `agotado` (p. ej. `empaquetado`) o expediente sobre el clearance: el UPDATE
    no casa y la respuesta es la misma en ambos casos."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [None, None]

    resp = await client.post(_URL, headers=TENANT_HEADERS)

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "expediente_not_found"
    assert _audit(mock_conn) == []


@pytest.mark.asyncio
async def test_reintento_exige_nivel_de_escritura(client_with_conn) -> None:
    """Sin `min_crud=3` el nivel por defecto (1, lectura) dejaría una escritura abierta:
    el gate pide el nivel 3."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False  # RBAC niega

    resp = await client.post(_URL, headers=TENANT_HEADERS)

    assert resp.status_code == 403
    # y la consulta del gate lleva el nivel mínimo 3
    assert any(3 in c.args for c in mock_conn.fetchval.await_args_list)
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_reintento_sin_identidad_es_denegado(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    headers = {k: v for k, v in TENANT_HEADERS.items() if k.lower() != "x-user-id"}

    resp = await client.post(_URL, headers=headers)

    assert resp.status_code in (401, 403)
    assert str(uuid.uuid4()) not in resp.text
