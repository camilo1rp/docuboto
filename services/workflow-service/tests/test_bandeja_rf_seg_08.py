# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Barrido RF-SEG-08/RF-BUS-10 de las lecturas de la bandeja de trámite
(continuación del cerrado en document-service/archive-service).

Cubre DOS huecos:
  1. No-read-up (RF-SEG-08) en `list_pending`/`list_inbox`/`get_history`/
     `get_current`: un radicado clasificado por encima del clearance del
     llamante no aparece en la bandeja/hoja de ruta NI cuenta en el total.
  2. Traza de lectura (RF-BUS-10): UNA entrada agregada por consulta de
     bandeja (nunca por fila) + UNA entrada por consulta de hoja de ruta.

Los inserts de `audit_log` se verifican inspeccionando las llamadas REALES a
`mock_conn.execute` (no se mockea `audit.append`), igual que
`tests/test_rbac_denial_audit.py` — así se ejercita la lógica real de
`orpycamcp_common.audit.append` (hash/prev_hash) contra la conexión mockeada.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import (
    RADICADO_ID,
    TENANT_HEADERS,
    TENANT_SLUG,
    TRACKING_NUMBER,
    USER_ID,
    make_event_record,
    make_step_record,
)


def _audit_insert_calls(mock_conn: AsyncMock) -> list:
    return [
        c
        for c in mock_conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]


# ---------------------------------------------------------------------------
# (a) No-read-up: el clearance llega al SQL de COUNT y de SELECT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_pending_clearance_reaches_count_and_select(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = None  # usuario sin clearance → PUBLICA(1)
    mock_conn.fetchval.return_value = 0
    mock_conn.fetch.return_value = []

    response = await client.get("/api/v1/workflows/pending", headers=TENANT_HEADERS)

    assert response.status_code == 200
    count_sql, *count_params = mock_conn.fetchval.call_args[0]
    assert "nivel_seguridad" in count_sql and "<=" in count_sql
    assert count_params[-1] == 1  # PUBLICA

    select_sql, *select_params = mock_conn.fetch.call_args[0]
    assert "nivel_seguridad" in select_sql and "<=" in select_sql
    assert 1 in select_params  # el mismo clearance también llega al SELECT de página


@pytest.mark.asyncio
async def test_inbox_clearance_reaches_count_and_select(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = None
    mock_conn.fetchval.return_value = 0
    mock_conn.fetch.return_value = []

    response = await client.get("/api/v1/workflows/inbox", headers=TENANT_HEADERS)

    assert response.status_code == 200
    count_sql, *count_params = mock_conn.fetchval.call_args[0]
    assert "nivel_seguridad" in count_sql and "<=" in count_sql
    assert count_params[-1] == 1

    select_sql, *select_params = mock_conn.fetch.call_args[0]
    assert "nivel_seguridad" in select_sql and "<=" in select_sql
    assert 1 in select_params


@pytest.mark.asyncio
async def test_flow_history_is_clearance_scoped(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = None  # PUBLICA(1)
    mock_conn.fetch.return_value = []

    response = await client.get(
        f"/api/v1/workflows/{RADICADO_ID}/history", headers=TENANT_HEADERS
    )

    assert response.status_code == 200
    select_sql, *select_params = mock_conn.fetch.call_args[0]
    assert "nivel_seguridad" in select_sql and "<=" in select_sql
    assert 1 in select_params


@pytest.mark.asyncio
async def test_get_current_step_is_clearance_scoped(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = None  # PUBLICA(1) — también gobierna get_current

    response = await client.get(
        f"/api/v1/workflows/{RADICADO_ID}/current", headers=TENANT_HEADERS
    )

    # 404: no_active_step (fetchrow devuelve None tanto para caller_clearance
    # como para el propio SELECT) — lo relevante es que la query llevó el filtro.
    assert response.status_code == 404
    select_sql, *select_params = mock_conn.fetchrow.call_args[0]
    assert "nivel_seguridad" in select_sql and "<=" in select_sql
    assert 1 in select_params


# ---------------------------------------------------------------------------
# (b) Fail-closed a PUBLICA sin X-User-Id (no rompe la petición)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_pending_without_user_id_defaults_to_publica(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = 0
    mock_conn.fetch.return_value = []
    headers = {"X-Tenant-Slug": TENANT_SLUG}  # sin X-User-Id

    response = await client.get("/api/v1/workflows/pending", headers=headers)

    assert response.status_code == 200
    # caller_clearance(None) devuelve PUBLICA sin tocar la BD (short-circuit).
    mock_conn.fetchrow.assert_not_awaited()
    count_sql, *count_params = mock_conn.fetchval.call_args[0]
    assert count_params[-1] == 1


@pytest.mark.asyncio
async def test_get_flow_history_without_user_id_defaults_to_publica(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = []
    headers = {"X-Tenant-Slug": TENANT_SLUG}  # sin X-User-Id

    response = await client.get(
        f"/api/v1/workflows/{RADICADO_ID}/history", headers=headers
    )

    assert response.status_code == 200
    mock_conn.fetchrow.assert_not_awaited()
    select_sql, *select_params = mock_conn.fetch.call_args[0]
    assert 1 in select_params


# ---------------------------------------------------------------------------
# (c) Traza agregada: UNA entrada por consulta de bandeja, no por fila
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_pending_audits_once_per_query_not_per_row(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = 3
    mock_conn.fetch.return_value = [
        make_step_record(),
        make_step_record(),
        make_step_record(),
    ]
    headers = {**TENANT_HEADERS, "X-Canal": "ui"}

    response = await client.get(
        "/api/v1/workflows/pending", params={"dept": "Jurídica"}, headers=headers
    )

    assert response.status_code == 200
    inserts = _audit_insert_calls(mock_conn)
    assert len(inserts) == 1  # UNA entrada, no una por cada uno de los 3 registros
    args = inserts[0].args
    # _INSERT_SQL: (sql, tenant, service, actor, canal, action, object_type, object_ref, payload, prev_hash, hash)
    assert args[1] == TENANT_SLUG
    assert args[2] == "workflow-service"
    assert args[3] == str(USER_ID)
    assert args[4] == "ui"
    assert args[5] == "workflow.bandeja_consultada"
    assert args[6] == "flow_step_busqueda"
    assert args[7] == "bandeja"
    import json as _json

    payload = _json.loads(args[8])
    assert payload["dept"] == "Jurídica"
    assert payload["total"] == 3


@pytest.mark.asyncio
async def test_inbox_audits_once_per_query(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = 2
    mock_conn.fetch.return_value = [make_step_record(), make_step_record()]
    headers = {**TENANT_HEADERS, "X-Canal": "mcp"}

    response = await client.get("/api/v1/workflows/inbox?box=entrada", headers=headers)

    assert response.status_code == 200
    inserts = _audit_insert_calls(mock_conn)
    assert len(inserts) == 1
    args = inserts[0].args
    assert args[4] == "mcp"
    assert args[5] == "workflow.bandeja_consultada"


# ---------------------------------------------------------------------------
# (d) Traza de hoja de ruta con object_ref correcto
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_flow_history_audits_with_radicado_object_ref(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = [make_step_record(step_number=1)]
    headers = {**TENANT_HEADERS, "X-Canal": "api"}

    response = await client.get(
        f"/api/v1/workflows/{RADICADO_ID}/history", headers=headers
    )

    assert response.status_code == 200
    inserts = _audit_insert_calls(mock_conn)
    assert len(inserts) == 1
    args = inserts[0].args
    assert args[3] == str(USER_ID)
    assert args[4] == "api"
    assert args[5] == "workflow.hoja_ruta_consultada"
    assert args[6] == "radicado"
    # object_ref = tracking_number (clave de negocio) cuando la lectura no es vacía
    assert args[7] == TRACKING_NUMBER


@pytest.mark.asyncio
async def test_get_flow_history_empty_falls_back_to_radicado_id(client_with_conn) -> None:
    # Hoja de ruta vacía (radicado sin pasos / por encima del clearance): no hay
    # tracking_number disponible -> object_ref cae al UUID (se registra el intento).
    client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = []
    headers = {**TENANT_HEADERS, "X-Canal": "api"}

    response = await client.get(f"/api/v1/workflows/{RADICADO_ID}/history", headers=headers)

    assert response.status_code == 200
    inserts = _audit_insert_calls(mock_conn)
    assert len(inserts) == 1  # se audita el intento aunque la hoja esté vacía
    assert inserts[0].args[7] == str(RADICADO_ID)  # fallback al UUID


# ---------------------------------------------------------------------------
# (e) Gate: sin user_id o canal no auditable NO audita
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_pending_without_canal_header_is_not_audited(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = 0
    mock_conn.fetch.return_value = []

    response = await client.get("/api/v1/workflows/pending", headers=TENANT_HEADERS)

    assert response.status_code == 200
    assert _audit_insert_calls(mock_conn) == []


@pytest.mark.asyncio
async def test_get_flow_history_without_user_id_is_not_audited(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = []
    headers = {"X-Tenant-Slug": TENANT_SLUG, "X-Canal": "ui"}  # sin X-User-Id

    response = await client.get(
        f"/api/v1/workflows/{RADICADO_ID}/history", headers=headers
    )

    assert response.status_code == 200
    assert _audit_insert_calls(mock_conn) == []


@pytest.mark.asyncio
async def test_inbox_with_unauditable_canal_is_not_audited(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = 0
    mock_conn.fetch.return_value = []
    headers = {**TENANT_HEADERS, "X-Canal": "system"}

    response = await client.get("/api/v1/workflows/inbox", headers=headers)

    assert response.status_code == 200
    assert _audit_insert_calls(mock_conn) == []


# ---------------------------------------------------------------------------
# (f) Best-effort: si audit.append lanza, la lectura sigue 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_pending_survives_audit_failure(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = 0
    mock_conn.fetch.return_value = []
    headers = {**TENANT_HEADERS, "X-Canal": "ui"}

    with patch(
        "app.services.workflow_service.audit.append",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        response = await client.get("/api/v1/workflows/pending", headers=headers)

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_flow_history_survives_audit_failure(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = []
    headers = {**TENANT_HEADERS, "X-Canal": "api"}

    with patch(
        "app.services.workflow_service.audit.append",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        response = await client.get(
            f"/api/v1/workflows/{RADICADO_ID}/history", headers=headers
        )

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# (g) Traza best-effort de GET /{id}/current y GET /{id}/events (hallazgo #3,
#     paridad RF-BUS-10 con get_history/bandeja — antes sin ninguna traza).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_current_step_audits_with_radicado_object_ref(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = make_step_record(step_status="in_progress")
    headers = {**TENANT_HEADERS, "X-Canal": "ui"}

    response = await client.get(
        f"/api/v1/workflows/{RADICADO_ID}/current", headers=headers
    )

    assert response.status_code == 200
    inserts = _audit_insert_calls(mock_conn)
    assert len(inserts) == 1  # UNA entrada, no una por cada campo/fila del paso
    args = inserts[0].args
    assert args[3] == str(USER_ID)
    assert args[4] == "ui"
    assert args[5] == "workflow.tramite_consultado"
    assert args[6] == "radicado"
    assert args[7] == TRACKING_NUMBER


@pytest.mark.asyncio
async def test_get_current_step_not_found_is_not_audited(client_with_conn) -> None:
    """Solo se audita tras una lectura EXITOSA — un 404 (sin paso activo) no
    representa una consulta satisfecha y no debe dejar traza de acceso."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = None
    headers = {**TENANT_HEADERS, "X-Canal": "ui"}

    response = await client.get(
        f"/api/v1/workflows/{RADICADO_ID}/current", headers=headers
    )

    assert response.status_code == 404
    assert _audit_insert_calls(mock_conn) == []


@pytest.mark.asyncio
async def test_get_current_step_without_canal_header_is_not_audited(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = make_step_record(step_status="pending")

    response = await client.get(
        f"/api/v1/workflows/{RADICADO_ID}/current", headers=TENANT_HEADERS
    )

    assert response.status_code == 200
    assert _audit_insert_calls(mock_conn) == []


@pytest.mark.asyncio
async def test_get_current_step_survives_audit_failure(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = make_step_record(step_status="pending")
    headers = {**TENANT_HEADERS, "X-Canal": "ui"}

    with patch(
        "app.services.workflow_service.audit.append",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        response = await client.get(
            f"/api/v1/workflows/{RADICADO_ID}/current", headers=headers
        )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_flow_events_audits_once_per_query(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = [
        make_event_record(tipo_tx="assign"),
        make_event_record(tipo_tx="complete"),
    ]
    headers = {**TENANT_HEADERS, "X-Canal": "mcp"}

    response = await client.get(
        f"/api/v1/workflows/{RADICADO_ID}/events", headers=headers
    )

    assert response.status_code == 200
    inserts = _audit_insert_calls(mock_conn)
    assert len(inserts) == 1  # UNA entrada, no una por cada uno de los 2 eventos
    args = inserts[0].args
    assert args[3] == str(USER_ID)
    assert args[4] == "mcp"
    assert args[5] == "workflow.tramite_consultado"
    assert args[6] == "radicado"
    assert args[7] == TRACKING_NUMBER


@pytest.mark.asyncio
async def test_get_flow_events_without_canal_header_is_not_audited(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = []

    response = await client.get(
        f"/api/v1/workflows/{RADICADO_ID}/events", headers=TENANT_HEADERS
    )

    assert response.status_code == 200
    assert _audit_insert_calls(mock_conn) == []


@pytest.mark.asyncio
async def test_get_flow_events_survives_audit_failure(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = []
    headers = {**TENANT_HEADERS, "X-Canal": "api"}

    with patch(
        "app.services.workflow_service.audit.append",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        response = await client.get(
            f"/api/v1/workflows/{RADICADO_ID}/events", headers=headers
        )

    assert response.status_code == 200
