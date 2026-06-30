# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from tests.conftest import TENANT_HEADERS, _FakeAcquire, make_db_conn, make_db_row


# --- envío manual + persistencia -------------------------------------------
@pytest.mark.asyncio
async def test_send_notification_manual(client: AsyncClient, db_conn) -> None:
    with patch("app.services.notification_service.send_email", new=AsyncMock()):
        response = await client.post(
            "/api/v1/notifications/send",
            json={
                "recipient_email": "user@example.com",
                "subject": "Test subject",
                "body": "Test body",
                "tenant_slug": "icetex",
            },
        )
    assert response.status_code == 202
    data = response.json()
    assert "id" in data
    assert data["status"] == "accepted"
    # Se persistió en la BD del tenant (un INSERT).
    db_conn.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_notification_failed_email_is_persisted(client: AsyncClient, db_conn) -> None:
    """Si el SMTP falla (todos los reintentos), igual se radica con status 'failed'."""
    with (
        patch(
            "app.services.notification_service.send_email",
            new=AsyncMock(side_effect=RuntimeError("smtp down")),
        ),
        patch("app.services.notification_service.asyncio.sleep", new=AsyncMock()),
    ):
        response = await client.post(
            "/api/v1/notifications/send",
            json={
                "recipient_email": "u@x.co",
                "subject": "S",
                "body": "B",
                "tenant_slug": "icetex",
            },
        )
    assert response.status_code == 202
    db_conn.execute.assert_awaited_once()


# --- reintentos SMTP (unitario) --------------------------------------------
@pytest.mark.asyncio
async def test_send_with_retries_succeeds_first_try() -> None:
    from app.schemas.notification import NotificationSend
    from app.services.notification_service import _send_with_retries

    with patch("app.services.notification_service.send_email", new=AsyncMock()) as mock_send:
        status, attempts, last_error = await _send_with_retries(
            NotificationSend(recipient_email="u@x.co", subject="S", body="B")
        )

    assert (status, attempts, last_error) == ("sent", 1, None)
    assert mock_send.await_count == 1


@pytest.mark.asyncio
async def test_send_with_retries_recovers_after_failures() -> None:
    from app.schemas.notification import NotificationSend
    from app.services.notification_service import _send_with_retries

    # Falla dos veces, tercer intento OK (SMTP_MAX_ATTEMPTS=3 por defecto).
    send = AsyncMock(side_effect=[RuntimeError("x"), RuntimeError("y"), None])
    with (
        patch("app.services.notification_service.send_email", new=send),
        patch("app.services.notification_service.asyncio.sleep", new=AsyncMock()) as sl,
    ):
        status, attempts, last_error = await _send_with_retries(
            NotificationSend(recipient_email="u@x.co", subject="S", body="B")
        )

    assert status == "sent"
    assert attempts == 3
    assert last_error is None
    assert send.await_count == 3
    assert sl.await_count == 2  # backoff entre los 3 intentos


@pytest.mark.asyncio
async def test_send_with_retries_exhausts_and_fails() -> None:
    from app.schemas.notification import NotificationSend
    from app.services.notification_service import _send_with_retries

    send = AsyncMock(side_effect=RuntimeError("smtp down"))
    with (
        patch("app.services.notification_service.send_email", new=send),
        patch("app.services.notification_service.asyncio.sleep", new=AsyncMock()),
    ):
        status, attempts, last_error = await _send_with_retries(
            NotificationSend(recipient_email="u@x.co", subject="S", body="B")
        )

    assert status == "failed"
    assert attempts == 3
    assert last_error == "smtp down"
    assert send.await_count == 3


# --- consulta del historial (BD por tenant) --------------------------------
@pytest.mark.asyncio
async def test_get_history(client: AsyncClient, db_conn) -> None:
    db_conn.fetch.return_value = [make_db_row(subject="Hola")]

    response = await client.get("/api/v1/notifications/history", headers=TENANT_HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["subject"] == "Hola"
    assert data[0]["tenant_slug"] == "icetex"


@pytest.mark.asyncio
async def test_get_history_empty(client: AsyncClient, db_conn) -> None:
    db_conn.fetch.return_value = []
    response = await client.get("/api/v1/notifications/history", headers=TENANT_HEADERS)
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_get_history_missing_tenant_is_400(client: AsyncClient) -> None:
    response = await client.get("/api/v1/notifications/history")
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "missing_tenant"


@pytest.mark.asyncio
async def test_get_notification_by_id(client: AsyncClient, db_conn) -> None:
    nid = uuid.uuid4()
    db_conn.fetchrow.return_value = make_db_row(nid=nid, event_type="workflow.step.created")

    response = await client.get(f"/api/v1/notifications/{nid}", headers=TENANT_HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(nid)
    assert data["event_type"] == "workflow.step.created"
    assert data["tenant_slug"] == "icetex"


@pytest.mark.asyncio
async def test_get_notification_not_found(client: AsyncClient, db_conn) -> None:
    db_conn.fetchrow.return_value = None
    response = await client.get(f"/api/v1/notifications/{uuid.uuid4()}", headers=TENANT_HEADERS)
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "notification_not_found"


# --- consumidores de eventos (sobre canónico) ------------------------------
@pytest.mark.asyncio
async def test_process_workflow_event() -> None:
    from orpycamcp_common import events

    # conn_dedup: used by event_consumer.acquire_tenant for the dedup claim.
    # fetchrow must return a non-None record so claim() returns True (event is new).
    conn_dedup = AsyncMock()
    conn_dedup.fetchrow.return_value = {"event_id": "claimed"}

    # conn: used by notification_service.acquire_tenant for persistence.
    conn = make_db_conn()
    with (
        patch("app.services.notification_service.send_email", new=AsyncMock()) as mock_send,
        patch(
            "app.services.event_consumer.acquire_tenant",
            return_value=_FakeAcquire(conn_dedup),
        ),
        patch(
            "app.services.notification_service.acquire_tenant",
            return_value=_FakeAcquire(conn),
        ),
    ):
        from app.services.event_consumer import process_event

        event = events.build_event(
            "workflow.step.created",
            tenant_slug="icetex",
            payload={
                "action": "assign",
                "tracking_number": "2024-ICETEX-E-000001",
                "to_dept": "juridica",
            },
        )
        await process_event(events.to_fields(event))

    mock_send.assert_awaited_once()
    assert mock_send.call_args[1]["to_addr"] == "juridica@orpycamcp.local"
    conn.execute.assert_awaited_once()  # persistido en BD


@pytest.mark.asyncio
async def test_process_workflow_overdue_event() -> None:
    from orpycamcp_common import events

    conn_dedup = AsyncMock()
    conn_dedup.fetchrow.return_value = {"event_id": "claimed"}

    conn = make_db_conn()
    with (
        patch("app.services.notification_service.send_email", new=AsyncMock()) as mock_send,
        patch(
            "app.services.event_consumer.acquire_tenant",
            return_value=_FakeAcquire(conn_dedup),
        ),
        patch(
            "app.services.notification_service.acquire_tenant",
            return_value=_FakeAcquire(conn),
        ),
    ):
        from app.services.event_consumer import process_event

        event = events.build_event(
            "workflow.step.overdue",
            tenant_slug="icetex",
            payload={
                "radicado_id": "uuid-1",
                "tracking_number": "2024-ICETEX-E-000099",
                "to_dept": "juridica",
                "due_at": "2024-01-10T00:00:00+00:00",
            },
        )
        await process_event(events.to_fields(event))

    mock_send.assert_awaited_once()
    assert mock_send.call_args[1]["to_addr"] == "juridica@orpycamcp.local"
    assert "VENCIDO" in mock_send.call_args[1]["subject"]


@pytest.mark.asyncio
async def test_process_document_event() -> None:
    from orpycamcp_common import events

    conn_dedup = AsyncMock()
    conn_dedup.fetchrow.return_value = {"event_id": "claimed"}

    conn = make_db_conn()
    with (
        patch("app.services.notification_service.send_email", new=AsyncMock()) as mock_send,
        patch(
            "app.services.event_consumer.acquire_tenant",
            return_value=_FakeAcquire(conn_dedup),
        ),
        patch(
            "app.services.notification_service.acquire_tenant",
            return_value=_FakeAcquire(conn),
        ),
    ):
        from app.services.event_consumer import process_document_event

        event = events.build_event(
            "document.radicado.created",
            tenant_slug="icetex",
            payload={
                "id": "uuid-1",
                "tracking_number": "2024-ICETEX-E-000042",
                "doc_type": "E",
                "dest_dept_code": 120,
                "due_date": None,
            },
        )
        await process_document_event(events.to_fields(event))

    mock_send.assert_awaited_once()
    assert mock_send.call_args[1]["to_addr"] == "dependencia-120@orpycamcp.local"


@pytest.mark.asyncio
async def test_process_document_event_ignores_other_types() -> None:
    from orpycamcp_common import events

    conn_dedup = AsyncMock()
    conn_dedup.fetchrow.return_value = {"event_id": "claimed"}

    with (
        patch("app.services.notification_service.send_email", new=AsyncMock()) as mock_send,
        patch(
            "app.services.event_consumer.acquire_tenant",
            return_value=_FakeAcquire(conn_dedup),
        ),
    ):
        from app.services.event_consumer import process_document_event

        event = events.build_event("document.radicado.updated", tenant_slug="icetex", payload={})
        await process_document_event(events.to_fields(event))

    mock_send.assert_not_awaited()


# --- health -----------------------------------------------------------------
@pytest.mark.asyncio
async def test_health_check(client: AsyncClient) -> None:
    smtp_instance = AsyncMock()
    smtp_instance.connect = AsyncMock()
    smtp_instance.quit = AsyncMock()

    with patch("app.routers.health.aiosmtplib.SMTP", return_value=smtp_instance):
        response = await client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["checks"]["redis"] == "ok"
    assert data["checks"]["smtp"] == "ok"
