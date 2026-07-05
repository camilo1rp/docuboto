# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests para /api/v1/batch (batch de expedientes) — cierre del gap RBAC.

`create_batch_expedientes` y `get_batch_status` deben exigir `USUA_PERM_EXPEDIENTE`
igual que `expedientes.py` (D-08): sin esto, un usuario del tenant sin el permiso
podía crear/vincular expedientes o leer resultados por esta ruta alternativa.

Mock call sequence — POST /api/v1/batch/expedientes (happy path):
  fetchval — require_permission gate                → True
  fetchval — INSERT batch_jobs RETURNING id          → job_id
  fetchval — SELECT created_at FROM batch_jobs       → NOW

Mock call sequence — GET /api/v1/batch/{job_id}/status (happy path):
  fetchval — require_permission gate                 → True
  fetchrow — SELECT batch_jobs                        → job row
  fetch    — SELECT batch_job_items                   → items
"""

import uuid
from datetime import UTC, datetime

import pytest

from tests.conftest import TENANT_HEADERS

NOW = datetime.now(tz=UTC)
JOB_ID = uuid.uuid4()


def _payload() -> dict:
    return {"expedientes": [{"name": "Expediente batch 1"}]}


def _job_row(**kw) -> dict:
    base = {
        "id": JOB_ID,
        "job_type": "expedientes",
        "status": "completed",
        "total_items": 1,
        "processed_items": 1,
        "failed_items": 0,
        "created_at": NOW,
        "started_at": NOW,
        "completed_at": NOW,
        "error_message": None,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# D-08: comprobaciones de autorización (USUA_PERM_EXPEDIENTE)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_batch_expedientes_sin_permiso_403(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None  # sin fila en auth_users → sin permiso
    resp = await client.post(
        "/api/v1/batch/expedientes", json=_payload(), headers=TENANT_HEADERS
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_create_batch_expedientes_sin_tenant_403(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    headers = {k: v for k, v in TENANT_HEADERS.items() if k != "X-Tenant-Slug"}
    resp = await client.post("/api/v1/batch/expedientes", json=_payload(), headers=headers)
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_get_batch_status_sin_permiso_403(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.get(f"/api/v1/batch/{JOB_ID}/status", headers=TENANT_HEADERS)
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_get_batch_status_sin_tenant_403(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    headers = {k: v for k, v in TENANT_HEADERS.items() if k != "X-Tenant-Slug"}
    resp = await client.get(f"/api/v1/batch/{JOB_ID}/status", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


# ---------------------------------------------------------------------------
# Happy path (con permiso concedido)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_batch_expedientes_202(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.side_effect = [True, JOB_ID, NOW]
    resp = await client.post(
        "/api/v1/batch/expedientes", json=_payload(), headers=TENANT_HEADERS
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["job_id"] == str(JOB_ID)
    assert body["status"] == "pending"
    assert body["total_items"] == 1


@pytest.mark.asyncio
async def test_get_batch_status_200(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # require_permission gate
    mock_conn.fetchrow.return_value = _job_row()
    mock_conn.fetch.return_value = []
    resp = await client.get(f"/api/v1/batch/{JOB_ID}/status", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == str(JOB_ID)
    assert body["status"] == "completed"


@pytest.mark.asyncio
async def test_get_batch_status_not_found_404(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # require_permission gate
    mock_conn.fetchrow.return_value = None  # job ausente
    resp = await client.get(f"/api/v1/batch/{JOB_ID}/status", headers=TENANT_HEADERS)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# BatchExpedienteCreate — pareja parcial dependencia productora (hallazgo Baja,
# cierre E15 perfil v4): debe rechazarse con 422 a nivel Pydantic, ANTES de
# tocar la BD, en vez de dejar que lo ataje el CHECK de la migración 019.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_batch_expedientes_productora_pareja_parcial_422(
    client_with_conn,
) -> None:
    """`dependencia_productora_codigos[i]` sin su `dependencia_productora_nombres[i]`
    (o viceversa) es un 422 de VALIDACIÓN Pydantic — el permiso se concede
    (`require_permission` pasa, único `fetchval`), pero el cuerpo inválido
    nunca llega al handler: ningún INSERT de `batch_jobs` se dispara (mismo
    espejo que `RadicadoLink`)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: solo el gate de require_permission
    rid = uuid.uuid4()
    payload = {
        "expedientes": [
            {
                "name": "Expediente batch productora parcial",
                "radicado_ids": [str(rid)],
                "dependencia_productora_codigos": [100],
                "dependencia_productora_nombres": [None],
            }
        ]
    }
    resp = await client.post("/api/v1/batch/expedientes", json=payload, headers=TENANT_HEADERS)
    assert resp.status_code == 422
    # Un único fetchval (el gate) — el INSERT INTO batch_jobs (también
    # fetchval, RETURNING id) nunca se ejecuta: la validación cortó ANTES.
    assert mock_conn.fetchval.call_count == 1


@pytest.mark.asyncio
async def test_create_batch_expedientes_productora_longitud_inconsistente_422(
    client_with_conn,
) -> None:
    """Una lista de productora no vacía cuya longitud no coincide con
    `radicado_ids` (listas paralelas por índice) también es 422."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    rid = uuid.uuid4()
    payload = {
        "expedientes": [
            {
                "name": "Expediente batch productora longitud",
                "radicado_ids": [str(rid)],
                "dependencia_productora_codigos": [100, 200],
                "dependencia_productora_nombres": ["Secretaría General", "Otra"],
            }
        ]
    }
    resp = await client.post("/api/v1/batch/expedientes", json=payload, headers=TENANT_HEADERS)
    assert resp.status_code == 422
    assert mock_conn.fetchval.call_count == 1
