# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de transferencias documentales (E12)."""

import uuid
from datetime import UTC, datetime

import pytest

from tests.conftest import TENANT_HEADERS, make_expediente_record

NOW = datetime.now(tz=UTC)


def _transfer_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "tipo": "primaria",
        "estado": "preparada",
        "expediente_id": uuid.uuid4(),
        "ubicacion_origen_id": None,
        "ubicacion_destino_id": None,
        "creada_por": "ana",
        "observacion": None,
        "created_at": NOW,
        "enviada_at": None,
        "recibida_at": None,
        "decidida_por": None,
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_create_requires_closed_expediente(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = make_expediente_record(exp_status="open")
    mock_conn.fetch.return_value = []

    resp = await client.post(
        "/api/v1/transferencias",
        json={"tipo": "primaria", "expediente_id": str(uuid.uuid4())},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "expediente_not_closed"


@pytest.mark.asyncio
async def test_create_transferencia(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    # get_expediente (closed) + fetch radicados, insert transferencia
    mock_conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        _transfer_row(expediente_id=exp_id),
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post(
        "/api/v1/transferencias",
        json={"tipo": "primaria", "expediente_id": str(exp_id)},
        headers={**TENANT_HEADERS, "X-Username": "ana"},
    )
    assert resp.status_code == 201
    assert resp.json()["estado"] == "preparada"


@pytest.mark.asyncio
async def test_enviar_then_recibir_freezes_expediente(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    # enviar: get(preparada) → update(enviada)
    mock_conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="preparada"),
        _transfer_row(id=tid, expediente_id=exp_id, estado="enviada", enviada_at=NOW),
    ]
    env = await client.post(f"/api/v1/transferencias/{tid}/enviar", headers=TENANT_HEADERS)
    assert env.status_code == 200
    assert env.json()["estado"] == "enviada"

    # recibir: get(enviada) → update(recibida) → transition_expediente(transferred)
    mock_conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="enviada"),
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida", recibida_at=NOW),
        make_expediente_record(exp_id=exp_id, exp_status="transferred"),
    ]
    mock_conn.fetch.return_value = []
    rec = await client.post(
        f"/api/v1/transferencias/{tid}/recibir",
        headers={**TENANT_HEADERS, "X-Username": "jefe"},
    )
    assert rec.status_code == 200
    assert rec.json()["estado"] == "recibida"


@pytest.mark.asyncio
async def test_recibir_invalid_from_preparada(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    tid = uuid.uuid4()
    mock_conn.fetchrow.return_value = _transfer_row(id=tid, estado="preparada")

    resp = await client.post(f"/api/v1/transferencias/{tid}/recibir", headers=TENANT_HEADERS)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "invalid_status_transition"


@pytest.mark.asyncio
async def test_transferencia_fuid(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    mock_conn.fetchrow.return_value = _transfer_row(id=tid, expediente_id=exp_id)
    mock_conn.fetch.return_value = [
        {
            "expediente_id": exp_id,
            "expediente_code": "EXP-2024-0001",
            "asunto": "Contrato",
            "fecha_inicial": NOW,
            "fecha_final": None,
            "serie_code": "100",
            "serie_name": "Contratos",
            "signatura": "DEP01-C0124",
            "unidad_tipo": "caja",
            "soporte": "papel",
            "num_folios": 200,
            "folio_inicio": 1,
            "folio_fin": 200,
        },
    ]
    resp = await client.get(f"/api/v1/transferencias/{tid}/fuid", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
