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

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.batch_service import BatchArchiveService
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


# ---------------------------------------------------------------------------
# process_batch_expedientes (tarea detached) — auditoría forense por expediente
# creado (E08 §9, Canal A). El router solo cubre el 202/403/422 de encolado;
# este bloque ejercita el propio `BatchArchiveService.process_batch_expedientes`
# a nivel de servicio, con una conexión mock propia (espejo de `tests/test_clearance.py`,
# que también construye `AsyncMock()` directamente en vez de pasar por el
# cliente HTTP — aquí no hay puerta HTTP para esta tarea, corre fuera del
# ciclo request/response).
# ---------------------------------------------------------------------------


def _item_payload(name: str) -> str:
    return json.dumps(
        {
            "name": name,
            "description": None,
            "trd_serie_id": None,
            "radicado_ids": [],
            "tracking_numbers": [],
            "dependencia_productora_codigos": [],
            "dependencia_productora_nombres": [],
        }
    )


def _audit_inserts(conn, action: str) -> list:
    """Espejo de `test_archive.py::_audit_inserts`: filtra por el patrón real
    de `audit.append` (`INSERT INTO public.audit_log`), no a ciegas — el
    propio `audit.append` añade su advisory lock (`execute`) + `fetchval` del
    `prev_hash` + el `INSERT` final; solo el último es el asiento en sí."""
    return [
        c.args
        for c in conn.execute.call_args_list
        if isinstance(c.args[0], str)
        and "INSERT INTO public.audit_log" in c.args[0]
        and c.args[5] == action
    ]


@pytest.mark.asyncio
async def test_process_batch_expedientes_emits_one_audit_entry_per_expediente() -> None:
    """`process_batch_expedientes` deja un asiento `archive.expediente_created`
    en `audit_log` por CADA expediente creado con éxito — espejo de
    `ArchiveService.create_expediente` (Ley 594/2000 arts. 21-24). Antes del
    fix el batch escribía sus propios INSERT sin pasar nunca por `audit.append`,
    un hueco de trazabilidad para la vía de creación masiva. Verifica: (1)
    N expedientes creados -> N asientos (ni 0 ni uno agregado), (2) `actor`
    es el `created_by` VALIDADO por el router (X-User-Id), NUNCA un campo del
    body, (3) el payload trae `nivel_seguridad`, (4) el asiento se escribe en
    `self.conn` — la conexión propia de la tarea detached — porque es la
    ÚNICA conexión a la que `process_batch_expedientes` tiene acceso (no
    recibe ni un pool ni la conexión request-scoped)."""
    conn = AsyncMock()
    # `async with conn.transaction():` dentro de `audit.append` (mismo patrón
    # que `tests/conftest.py::_make_mock_conn`).
    conn.transaction = MagicMock(return_value=AsyncMock())

    job_id = uuid.uuid4()
    created_by = uuid.uuid4()
    exp_ids = [uuid.uuid4(), uuid.uuid4()]
    item_ids = [uuid.uuid4(), uuid.uuid4()]

    items = [
        {"id": item_ids[0], "item_index": 0, "payload": _item_payload("Expediente batch 1")},
        {"id": item_ids[1], "item_index": 1, "payload": _item_payload("Expediente batch 2")},
    ]
    conn.fetch.return_value = items

    seq_counter = {"n": 0}

    async def _fetchval(query, *args, **kwargs):
        if "RETURNING created_by" in query:
            return created_by
        if "INSERT INTO expediente_sequences" in query:
            seq_counter["n"] += 1
            return seq_counter["n"]
        if "SELECT hash" in query:  # audit.append: prev_hash lookup
            return None
        raise AssertionError(f"fetchval inesperado en el test: {query}")

    conn.fetchval.side_effect = _fetchval

    exp_rows = iter(
        [
            {"id": exp_ids[0], "nivel_seguridad": 1},
            {"id": exp_ids[1], "nivel_seguridad": 2},
        ]
    )
    final_row = {"failed_items": 0}

    async def _fetchrow(query, *args, **kwargs):
        if "INSERT INTO expedientes" in query:
            return next(exp_rows)
        if "SELECT failed_items" in query:
            return final_row
        raise AssertionError(f"fetchrow inesperado en el test: {query}")

    conn.fetchrow.side_effect = _fetchrow

    service = BatchArchiveService(conn, "demo")
    await service.process_batch_expedientes(job_id)

    # Ningún ítem falló silenciosamente enmascarando una query no enrutada
    # (`except Exception` del bucle atraparía un `AssertionError` de los
    # routers de arriba y lo convertiría en 'failed', dejando `audit_calls`
    # vacío en vez de fallar con un traceback claro).
    failed_updates = [
        c.args
        for c in conn.execute.call_args_list
        if isinstance(c.args[0], str) and "status='failed'" in c.args[0]
    ]
    assert failed_updates == []

    audit_calls = _audit_inserts(conn, "archive.expediente_created")
    assert len(audit_calls) == 2  # N expedientes creados -> N asientos

    year = datetime.now(tz=UTC).year
    for call, exp_id, nivel in zip(audit_calls, exp_ids, (1, 2), strict=True):
        actor, object_type, object_ref, payload_json = call[3], call[6], call[7], call[8]
        assert actor == str(created_by)  # X-User-Id validado por el router, no del body
        assert object_type == "expediente"
        assert object_ref.startswith(f"EXP-{year}-")
        payload = json.loads(payload_json)
        assert payload["id"] == str(exp_id)
        assert payload["nivel_seguridad"] == nivel
