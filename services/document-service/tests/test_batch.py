# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests para /api/v1/batch (batch de documentos/radicados).

Reescrita (deuda Fase 6, cerrada 2026-08): la suite original entera estaba
bajo `pytest.mark.skip` — referenciaba una fixture `test_client` que nunca
existió en `conftest.py` (las fixtures reales son `client`/`client_with_conn`,
ver `tests/conftest.py`) y no enviaba `X-Tenant-Slug`/`X-User-Id`, que
`get_tenant_conn`/`require_permission` exigen. Se reescribe sobre
`client_with_conn` (mismo patrón que `test_metadata_rbac_audit.py` y el
gemelo `archive-service/tests/test_batch.py`), cubriendo el gate
`PERM_RADI` recién añadido a ambas rutas, la ruta nueva de estado
(`GET /api/v1/batch/documents/{job_id}/status`) y los asientos de auditoría
de `process_batch_documents` (hueco cerrado en `batch_service.py`).

Mock call sequence — POST /api/v1/batch/documents (happy path):
  fetchval — require_permission gate                → True
  fetchval — INSERT batch_jobs RETURNING id          → job_id
  fetchval — SELECT created_at FROM batch_jobs       → NOW

Mock call sequence — GET /api/v1/batch/documents/{job_id}/status (happy path):
  fetchval — require_permission gate                 → True
  fetchrow — SELECT batch_jobs                        → job row
  fetch    — SELECT batch_job_items                   → items
"""

import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.batch_service import BatchService
from tests.conftest import TENANT_HEADERS

NOW = datetime.now(tz=UTC)
JOB_ID = uuid.uuid4()


def _payload() -> dict:
    return {
        "documents": [
            {
                "doc_type": "E",
                "subject": "Solicitud de información",
                "doc_class": "Petición",
                "dest_dept_code": 1,
            }
        ]
    }


def _job_row(**kw) -> dict:
    base = {
        "id": JOB_ID,
        "job_type": "documents",
        "status": "completed",
        "total_items": 1,
        "processed_items": 1,
        "failed_items": 0,
        "created_at": NOW,
        "started_at": NOW,
        "completed_at": NOW,
        "error_message": None,
        "reanudaciones": 0,
        "reprocesado_at": None,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# Autorización — PERM_RADI (app/routers/batch.py:33 y :74)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_batch_documents_sin_permiso_403(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None  # sin fila en auth_users → sin permiso
    resp = await client.post("/api/v1/batch/documents", json=_payload(), headers=TENANT_HEADERS)
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_create_batch_documents_sin_tenant_400(client_with_conn) -> None:
    """`create_batch_documents` valida `X-Tenant-Slug` en el propio handler
    (tras `require_permission`, que aquí no depende del tenant) — 400, no
    403; con `get_tenant_conn` real (sin mockear) el mismo header ausente ya
    habría cortado antes, en la propia dependencia."""
    client, mock_conn = client_with_conn
    headers = {k: v for k, v in TENANT_HEADERS.items() if k != "X-Tenant-Slug"}
    resp = await client.post("/api/v1/batch/documents", json=_payload(), headers=headers)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_get_batch_status_sin_permiso_403(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.get(f"/api/v1/batch/documents/{JOB_ID}/status", headers=TENANT_HEADERS)
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_get_batch_status_sin_tenant_400(client_with_conn) -> None:
    """Espejo de `test_create_batch_documents_sin_tenant_400`: el handler
    valida `X-Tenant-Slug` él mismo (400), no vía el gate RBAC (403)."""
    client, mock_conn = client_with_conn
    headers = {k: v for k, v in TENANT_HEADERS.items() if k != "X-Tenant-Slug"}
    resp = await client.get(f"/api/v1/batch/documents/{JOB_ID}/status", headers=headers)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Happy path (con permiso concedido)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_batch_documents_202(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.side_effect = [True, JOB_ID, NOW]
    resp = await client.post("/api/v1/batch/documents", json=_payload(), headers=TENANT_HEADERS)
    assert resp.status_code == 202
    body = resp.json()
    assert body["job_id"] == str(JOB_ID)
    assert body["job_type"] == "documents"
    assert body["status"] == "pending"
    assert body["total_items"] == 1


@pytest.mark.asyncio
async def test_create_batch_documents_too_few_422(client_with_conn) -> None:
    """min_length=1 en `BatchDocumentsRequest.documents` — rechazado ANTES
    de tocar la BD (ningún fetchval del gate ni del INSERT)."""
    client, mock_conn = client_with_conn
    resp = await client.post(
        "/api/v1/batch/documents", json={"documents": []}, headers=TENANT_HEADERS
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_batch_documents_too_many_422(client_with_conn) -> None:
    """max_length=1000 — 1001 documentos es 422."""
    client, mock_conn = client_with_conn
    payload = {
        "documents": [
            {"doc_type": "E", "subject": f"Documento {i}", "doc_class": "P", "dest_dept_code": 1}
            for i in range(1001)
        ]
    }
    resp = await client.post("/api/v1/batch/documents", json=payload, headers=TENANT_HEADERS)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_batch_status_200(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # require_permission gate
    mock_conn.fetchrow.return_value = _job_row()
    mock_conn.fetch.return_value = []
    resp = await client.get(f"/api/v1/batch/documents/{JOB_ID}/status", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == str(JOB_ID)
    assert body["status"] == "completed"
    assert body["total_items"] == 1


@pytest.mark.asyncio
async def test_get_batch_status_not_found_404(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # require_permission gate
    mock_conn.fetchrow.return_value = None  # job ausente
    resp = await client.get(f"/api/v1/batch/documents/{JOB_ID}/status", headers=TENANT_HEADERS)
    assert resp.status_code == 404
    # 404 NEUTRO: el mismo para un lote inexistente y para uno de otra persona.
    assert resp.json()["detail"]["error"] == "lote_no_encontrado"


@pytest.mark.asyncio
async def test_get_batch_status_sin_usuario_400(client_with_conn) -> None:
    """El alcance del lote (creador / PERM_RADI nivel 3) necesita saber quién pregunta."""
    client, mock_conn = client_with_conn
    headers = {k: v for k, v in TENANT_HEADERS.items() if k != "X-User-Id"}
    resp = await client.get(f"/api/v1/batch/documents/{JOB_ID}/status", headers=headers)
    assert resp.status_code in (400, 403)


@pytest.mark.asyncio
async def test_el_estado_pide_el_alcance_en_el_where_y_el_nivel_en_los_items(client_with_conn) -> None:
    """El predicado de alcance y el gate del `subject` viajan en el SQL (no en una
    comprobación posterior): creador o `PERM_RADI`>=3, y `nivel_seguridad <= clearance`."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = _job_row()
    mock_conn.fetch.return_value = []
    resp = await client.get(f"/api/v1/batch/documents/{JOB_ID}/status", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    sqls = [c.args[0] for c in mock_conn.fetchrow.await_args_list] + [
        c.args[0] for c in mock_conn.fetch.await_args_list
    ]
    assert any("created_by = $2 OR $3::boolean" in q and "job_type = 'documents'" in q for q in sqls)
    assert any("LEFT JOIN radicados r ON r.id = i.result_id" in q and "r.nivel_seguridad <= $2" in q for q in sqls)


@pytest.mark.asyncio
async def test_reprocesar_de_un_lote_ajeno_es_el_404_neutro(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.side_effect = [True, True, None]  # gate RBAC, nivel 3, lote no visible
    resp = await client.post(f"/api/v1/batch/documents/{JOB_ID}/reprocesar", headers=TENANT_HEADERS)
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "lote_no_encontrado"


# ---------------------------------------------------------------------------
# process_batch_documents (tarea detached) — auditoría forense por radicado
# creado (hueco cerrado, `batch_service.py`). El router solo cubre el
# 202/403/422 de encolado; este bloque ejercita el propio
# `BatchService.process_batch_documents` a nivel de servicio, con una
# conexión mock propia (espejo de `archive-service/tests/test_batch.py`).
# ---------------------------------------------------------------------------


def _item_payload(subject: str, doc_type: str = "E") -> str:
    return json.dumps(
        {
            "doc_type": doc_type,
            "subject": subject,
            "doc_class": "Petición",
            "dest_dept_code": 1,
            "dest_dept": None,
            "sender_name": None,
            "sender_entity": None,
            "pages": None,
            "observations": None,
        }
    )


def _audit_inserts(conn, action: str) -> list:
    """Filtra por el patrón real de `audit.append`
    (`INSERT INTO public.audit_log`), no a ciegas — `audit.append` añade su
    advisory lock (`execute`) + `fetchval` del `prev_hash` + el `INSERT`
    final; solo el último es el asiento en sí (espejo de
    `archive-service/tests/test_batch.py::_audit_inserts`)."""
    return [
        c.args
        for c in conn.execute.call_args_list
        if isinstance(c.args[0], str)
        and "INSERT INTO public.audit_log" in c.args[0]
        and c.args[5] == action
    ]


@pytest.mark.asyncio
async def test_process_batch_documents_emits_one_audit_entry_per_radicado() -> None:
    """`process_batch_documents` deja un asiento `document.radicado_created`
    en `audit_log` por CADA radicado creado con éxito — espejo de
    `DocumentService.create` (Ley 594/2000 arts. 21-24). Antes del fix el
    batch nunca pasaba por `audit.append`, un hueco de trazabilidad para la
    vía de creación masiva de documentos oficiales. Verifica: (1) N
    documentos creados -> N asientos, (2) `actor` es el `created_by`
    recuperado de `batch_jobs` (X-User-Id validado por el router al
    encolar), NUNCA un campo del body, (3) el payload trae `nivel_seguridad`,
    (4) el asiento se escribe en `self.conn` — la única conexión disponible
    para esta tarea detached."""
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())

    job_id = uuid.uuid4()
    created_by = uuid.uuid4()
    radicado_ids = [uuid.uuid4(), uuid.uuid4()]
    item_ids = [uuid.uuid4(), uuid.uuid4()]

    items = [
        {"id": item_ids[0], "item_index": 0, "payload": _item_payload("Documento batch 1")},
        {"id": item_ids[1], "item_index": 1, "payload": _item_payload("Documento batch 2")},
    ]
    conn.fetch.return_value = items

    seq_counter = {"n": 0}

    async def _fetchval(query, *args, **kwargs):
        if "pg_try_advisory_lock" in query or "pg_advisory_unlock" in query:
            return True
        if "FROM batch_job_items WHERE id" in query:  # reclamo del ítem (FOR UPDATE)
            return "pending"
        if "RETURNING COALESCE(reprocesado_por, created_by)" in query:
            return created_by
        if "FROM dependencias WHERE codigo" in query:
            return "Dirección General"
        if "FROM tipos_documentales" in query:  # catálogo de tipos (B6)
            return "Tipo"
        if "SELECT code FROM tenants" in query:
            return "DEMO"
        if "INSERT INTO tracking_sequences" in query:
            seq_counter["n"] += 1
            return seq_counter["n"]
        if "SELECT hash" in query:  # audit.append: prev_hash lookup
            return None
        raise AssertionError(f"fetchval inesperado en el test: {query}")

    conn.fetchval.side_effect = _fetchval

    radicado_rows = iter(
        [
            {"id": radicado_ids[0], "nivel_seguridad": 1},
            {"id": radicado_ids[1], "nivel_seguridad": 1},
        ]
    )
    final_row = {"total_items": 2, "processed_items": 2, "failed_items": 0}

    async def _fetchrow(query, *args, **kwargs):
        if "FROM metadata_templates" in query:
            return None
        if "INSERT INTO radicados" in query:
            return next(radicado_rows)
        if "SELECT total_items" in query:
            return final_row
        raise AssertionError(f"fetchrow inesperado en el test: {query}")

    conn.fetchrow.side_effect = _fetchrow

    service = BatchService(conn, "demo")
    await service.process_batch_documents(job_id)

    # Ningún ítem falló silenciosamente enmascarando una query no enrutada.
    failed_updates = [
        c.args
        for c in conn.execute.call_args_list
        if isinstance(c.args[0], str)
        and "UPDATE batch_job_items" in c.args[0]
        and "status = 'failed'" in c.args[0]
    ]
    assert failed_updates == []

    audit_calls = _audit_inserts(conn, "document.radicado_created")
    assert len(audit_calls) == 2  # N radicados creados -> N asientos

    year = datetime.now(tz=UTC).year
    for call, radicado_id in zip(audit_calls, radicado_ids, strict=True):
        actor, object_type, object_ref, payload_json = call[3], call[6], call[7], call[8]
        assert actor == str(created_by)  # created_by de batch_jobs, no del body
        assert object_type == "radicado"
        assert object_ref.startswith(f"{year}-DEMO-E-")
        payload = json.loads(payload_json)
        assert payload["id"] == str(radicado_id)
        assert payload["nivel_seguridad"] == 1
        assert payload["doc_type"] == "E"


@pytest.mark.asyncio
async def test_process_batch_documents_item_failure_marks_failed_outside_tx() -> None:
    """Ítem que falla A MITAD de su propia transacción (aquí: en el propio
    `INSERT INTO radicados`, ANTES de llegar a `audit.append`) dispara el
    `except Exception` de `process_batch_documents`, que queda FUERA del
    `async with self.conn.transaction():` del ítem — mismo patrón
    estructural que `BatchArchiveService.process_batch_expedientes`.

    Verifica, con dos ítems (1 exitoso, 2 fallido):
    (a) el ítem 1 recibe `status = 'success'` y deja su asiento
        `document.radicado_created`;
    (b) el ítem 2 recibe `status = 'failed'`;
    (c) sin doble conteo;
    (d) el asiento de auditoría SOLO existe para el ítem exitoso — el
        resultado final refleja lo que REALMENTE entró, no la intención
        del lote."""
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())

    job_id = uuid.uuid4()
    created_by = uuid.uuid4()
    radicado_ids = [uuid.uuid4()]
    item_ids = [uuid.uuid4(), uuid.uuid4()]

    items = [
        {"id": item_ids[0], "item_index": 0, "payload": _item_payload("Documento batch 1")},
        {"id": item_ids[1], "item_index": 1, "payload": _item_payload("Documento batch 2")},
    ]
    conn.fetch.return_value = items

    seq_counter = {"n": 0}

    async def _fetchval(query, *args, **kwargs):
        if "pg_try_advisory_lock" in query or "pg_advisory_unlock" in query:
            return True
        if "FROM batch_job_items WHERE id" in query:  # reclamo del ítem (FOR UPDATE)
            return "pending"
        if "RETURNING COALESCE(reprocesado_por, created_by)" in query:
            return created_by
        if "FROM dependencias WHERE codigo" in query:
            return "Dirección General"
        if "FROM tipos_documentales" in query:  # catálogo de tipos (B6)
            return "Tipo"
        if "SELECT code FROM tenants" in query:
            return "DEMO"
        if "INSERT INTO tracking_sequences" in query:
            seq_counter["n"] += 1
            return seq_counter["n"]
        if "SELECT hash" in query:  # audit.append: prev_hash lookup (solo ítem 1)
            return None
        raise AssertionError(f"fetchval inesperado en el test: {query}")

    conn.fetchval.side_effect = _fetchval

    radicado_rows = iter([{"id": radicado_ids[0], "nivel_seguridad": 1}])
    final_row = {"total_items": 2, "processed_items": 1, "failed_items": 1}
    insert_count = {"n": 0}

    async def _fetchrow(query, *args, **kwargs):
        if "FROM metadata_templates" in query:
            return None
        if "INSERT INTO radicados" in query:
            insert_count["n"] += 1
            if insert_count["n"] == 2:
                # ítem 2: falla EN el propio INSERT del radicado — antes de
                # marcar éxito y de `audit.append`.
                raise RuntimeError("radicado insert failed (simulated)")
            return next(radicado_rows)
        if "SELECT total_items" in query:
            return final_row
        raise AssertionError(f"fetchrow inesperado en el test: {query}")

    conn.fetchrow.side_effect = _fetchrow

    service = BatchService(conn, "demo")
    await service.process_batch_documents(job_id)

    def _execute_calls(pattern: str) -> list:
        return [
            c.args
            for c in conn.execute.call_args_list
            if isinstance(c.args[0], str)
            and "UPDATE batch_job_items" in c.args[0]
            and pattern in c.args[0]
        ]

    success_updates = _execute_calls("status = 'success'")
    failed_updates = _execute_calls("status = 'failed'")

    # (a) ítem 1: éxito.
    assert len(success_updates) == 1
    assert success_updates[0][1] == radicado_ids[0]  # result_id
    assert success_updates[0][2] == item_ids[0]

    # (b) ítem 2: fallido, marcado FUERA de la tx (el `except` sí se ejecutó).
    assert len(failed_updates) == 1
    assert failed_updates[0][2] == item_ids[1]

    # (c) sin doble conteo.
    assert len(success_updates) + len(failed_updates) == 2

    # (d) el asiento de auditoría SOLO existe para el ítem exitoso.
    audit_calls = _audit_inserts(conn, "document.radicado_created")
    assert len(audit_calls) == 1
    payload = json.loads(audit_calls[0][8])
    assert payload["id"] == str(radicado_ids[0])


class TestLaTareaDeFondoNoUsaLaConexionDelRequest:
    """El lote se procesa en su PROPIA conexión, y su fallo queda en el job.

    Estos tests existen porque el defecto que cierran vivía en el router y era
    invisible desde el servicio: `create_batch_documents` encolaba el job y
    lanzaba `asyncio.create_task(service.process_batch_documents(job_id))` con
    el mismo `service` —y por tanto la misma conexión— que servía la petición.
    Con Postgres real esa tarea moría en el acto (`InterfaceError: another
    operation is in progress`) y, al no recogerla nadie, el lote se quedaba en
    «Pendiente 0/N» para siempre. Con conexiones simuladas no se notaba nada:
    un mock atiende dos operaciones a la vez sin protestar.

    El tier de integración comprueba que el lote TERMINA; lo que se comprueba
    aquí es lo otro, que solo se ve desde el router: de dónde sale la conexión
    con la que se procesa, y qué queda escrito cuando la tarea revienta.
    """

    async def test_procesar_lote_abre_su_propia_conexion_del_pool(self, monkeypatch):
        """`_procesar_lote` pide conexión al pool por tenant, no la recibe."""
        from app.routers import batch as batch_router

        conexion_del_pool = AsyncMock()
        slugs_pedidos: list[str] = []

        @asynccontextmanager
        async def falso_acquire(slug: str):
            slugs_pedidos.append(slug)
            yield conexion_del_pool

        usados: list[object] = []

        class ServicioEspia:
            def __init__(self, conn, slug):
                usados.append(conn)

            async def process_batch_documents(self, job_id):
                return None

        monkeypatch.setattr(batch_router, "acquire_tenant", falso_acquire)
        monkeypatch.setattr(batch_router, "BatchService", ServicioEspia)

        await batch_router._procesar_lote("demo", uuid4())

        assert slugs_pedidos == ["demo"], (
            "la tarea de fondo debe adquirir su conexión por tenant; si no pide "
            "ninguna, está reutilizando la del request"
        )
        assert usados == [conexion_del_pool]

    async def test_si_la_tarea_revienta_el_job_queda_failed_con_su_motivo(self, monkeypatch):
        """Un fallo fuera del ciclo petición/respuesta se ESCRIBE en el job.

        Sin esto, el log lo ve quien administra el servidor y la persona que
        lanzó el lote se queda mirando un «pendiente» que no va a cambiar
        nunca: un job reventado y uno trabajando eran indistinguibles.
        """
        from app.routers import batch as batch_router

        conexion = AsyncMock()

        @asynccontextmanager
        async def falso_acquire(slug: str):
            yield conexion

        class ServicioQueRevienta:
            def __init__(self, conn, slug):
                pass

            async def process_batch_documents(self, job_id):
                raise RuntimeError("la conexión se cerró a media escritura")

        monkeypatch.setattr(batch_router, "acquire_tenant", falso_acquire)
        monkeypatch.setattr(batch_router, "BatchService", ServicioQueRevienta)

        job_id = uuid4()
        await batch_router._procesar_lote("demo", job_id)

        escrituras = [str(c) for c in conexion.execute.await_args_list]
        assert any("status = 'failed'" in e for e in escrituras), (
            f"el job debía quedar marcado como fallido; escrituras: {escrituras}"
        )
        assert any("la conexión se cerró a media escritura" in e for e in escrituras), (
            "el motivo del fallo tiene que llegar al job, no solo al log"
        )
