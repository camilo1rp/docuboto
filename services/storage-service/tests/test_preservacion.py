# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de preservación digital: plan + eventos (E10)."""

import json
import uuid
from datetime import UTC, datetime

import pytest

from tests.conftest import TENANT_HEADERS

NOW = datetime.now(tz=UTC)


def _plan_row(version: int = 1) -> dict:
    return {
        "id": uuid.uuid4(),
        "version": version,
        "formatos_destino": ["PDF/A-2b", "TIFF"],
        "num_copias": 2,
        "periodicidad_fixity_dias": 180,
        "politica_migracion": "anual",
        "contingencia": None,
        "creado_por": "ana",
        "created_at": NOW,
    }


@pytest.mark.asyncio
async def test_set_plan_versions(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    # 1.º fetchval = gate require_permission (concedido); 2.º = MAX(version) → 0.
    mock_conn.fetchval.side_effect = [True, 0]
    mock_conn.fetchrow.return_value = _plan_row(version=1)

    resp = await client.put(
        "/api/v1/preservacion/plan",
        json={
            "formatos_destino": ["PDF/A-2b", "TIFF"],
            "num_copias": 2,
            "periodicidad_fixity_dias": 180,
            "politica_migracion": "anual",
        },
        headers={**TENANT_HEADERS, "X-Username": "ana"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["version"] == 1
    assert body["num_copias"] == 2


@pytest.mark.asyncio
async def test_get_plan_not_found(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = True  # check_permission → concedido
    mock_conn.fetchrow.return_value = None
    resp = await client.get("/api/v1/preservacion/plan", headers=TENANT_HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_plan_forbidden_without_permission(client_with_conn) -> None:
    """GET /plan expone `politica_migracion`/`contingencia` — gateado igual
    que `GET /aip/{expediente_id}` (hallazgo de auditoría Increment B)."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = False  # check_permission → denegado
    resp = await client.get("/api/v1/preservacion/plan", headers=TENANT_HEADERS)
    assert resp.status_code == 403
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_record_evento(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = True  # gate require_permission → concedido
    exp_ref = str(uuid.uuid4())
    h = "a" * 64
    mock_conn.fetchrow.return_value = {
        "id": uuid.uuid4(),
        "object_ref": exp_ref,
        "tipo": "FIJACION",
        "resultado": "ok",
        "hash_before": h,
        "hash_after": h,
        "detalle": json.dumps({"algoritmo": "SHA-256"}),
        "agente": "system",
        "ts": NOW,
    }
    resp = await client.post(
        "/api/v1/preservacion/eventos",
        json={
            "object_ref": exp_ref,
            "tipo": "FIJACION",
            "resultado": "ok",
            "hash_before": h,
            "hash_after": h,
            "detalle": {"algoritmo": "SHA-256"},
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["tipo"] == "FIJACION"
    assert body["detalle"]["algoritmo"] == "SHA-256"  # JSONB parseado


@pytest.mark.asyncio
async def test_record_evento_forbidden_without_permission(client_with_conn) -> None:
    """POST /eventos es una ESCRITURA a la traza de preservación: sin permiso
    no debe poder inyectar eventos PREMIS falsos (hallazgo de auditoría R1,
    endurecimiento de gate del router de preservación)."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = False  # check_permission → denegado
    resp = await client.post(
        "/api/v1/preservacion/eventos",
        json={"object_ref": str(uuid.uuid4()), "tipo": "WORM", "resultado": "ok"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 403
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_list_eventos(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = True  # check_permission → concedido
    mock_conn.fetch.return_value = [
        {
            "id": uuid.uuid4(),
            "object_ref": "exp-1",
            "tipo": "INGESTA",
            "resultado": "ok",
            "hash_before": None,
            "hash_after": None,
            "detalle": None,
            "agente": "system",
            "ts": NOW,
        },
    ]
    resp = await client.get("/api/v1/preservacion/eventos?object_ref=exp-1", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()[0]["tipo"] == "INGESTA"


@pytest.mark.asyncio
async def test_list_eventos_forbidden_without_permission(client_with_conn) -> None:
    """GET /eventos ya filtra `hash_before`/`hash_after`/`object_ref` por
    expediente — mismo gate de lectura que `GET /aip/{expediente_id}`
    (hallazgo de auditoría Increment B)."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = False  # check_permission → denegado
    resp = await client.get("/api/v1/preservacion/eventos?object_ref=exp-1", headers=TENANT_HEADERS)
    assert resp.status_code == 403
    mock_conn.fetch.assert_not_called()


# El AIP real (BagIt + PREMIS v3, E10 Increment B) tiene su propia suite:
# ver tests/test_aip_real.py — el stub previo (texto sintético, sin RBAC ni
# bytes reales) fue reemplazado por completo.
