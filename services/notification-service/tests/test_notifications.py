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
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from tests.conftest import (
    INTERNAL_HEADERS,
    TENANT_HEADERS,
    USER,
    _FakeAcquire,
    make_db_conn,
    make_db_row,
)


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
            headers=INTERNAL_HEADERS,
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
            headers=INTERNAL_HEADERS,
        )
    assert response.status_code == 202
    db_conn.execute.assert_awaited_once()


# --- RF-FIR-13: cierre de fuga de OTP (sensible=True nunca se persiste) ----
@pytest.mark.asyncio
async def test_sensible_notification_sends_real_body_but_persists_redacted(
    client: AsyncClient, db_conn
) -> None:
    """El SMTP recibe el OTP real; la fila persistida lleva el placeholder.

    Reproduce RF-FIR-13 (E06 Inc.2): signature-service manda el OTP en el
    body y marca `sensible=True`. `send_email()` debe ver el body real
    (inherente al 2FA por correo); `notifications.body`, en cambio, NUNCA
    debe contener el código.
    """
    otp_body = "Código: 123456\n\nVálido por 5 minutos."
    with patch("app.services.notification_service.send_email", new=AsyncMock()) as mock_send:
        response = await client.post(
            "/api/v1/notifications/send",
            json={
                "recipient_email": "firmante@example.com",
                "subject": "Código de verificación para su firma electrónica",
                "body": otp_body,
                "tenant_slug": "icetex",
                "sensible": True,
            },
            headers=INTERNAL_HEADERS,
        )
    assert response.status_code == 202

    # El envío SMTP SÍ recibió el OTP real.
    assert mock_send.call_args.kwargs["body"] == otp_body

    # Lo persistido NUNCA es el OTP real.
    db_conn.execute.assert_awaited_once()
    persisted_args = db_conn.execute.call_args.args
    persisted_body = persisted_args[4]  # INSERT ... VALUES ($1..$4=body,...)
    persisted_sensible = persisted_args[10]  # último parámetro ($10=sensible)
    assert persisted_body != otp_body
    assert "123456" not in persisted_body
    assert persisted_body == "[contenido sensible omitido]"
    assert persisted_sensible is True


@pytest.mark.asyncio
async def test_non_sensible_notification_persists_real_body(
    client: AsyncClient, db_conn
) -> None:
    """Retrocompatibilidad: `sensible` ausente/False -> body real persistido (como hoy)."""
    with patch("app.services.notification_service.send_email", new=AsyncMock()):
        response = await client.post(
            "/api/v1/notifications/send",
            json={
                "recipient_email": "u@x.co",
                "subject": "S",
                "body": "Cuerpo normal, no sensible",
                "tenant_slug": "icetex",
            },
            headers=INTERNAL_HEADERS,
        )
    assert response.status_code == 202

    persisted_args = db_conn.execute.call_args.args
    assert persisted_args[4] == "Cuerpo normal, no sensible"
    assert persisted_args[10] is False


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
    # /history exige ahora USUA_PERM_ADMIN → requiere identidad (X-User-Id). Con
    # identidad pero sin tenant, sigue devolviendo el 400 missing_tenant original.
    response = await client.get(
        "/api/v1/notifications/history", headers={"X-User-Id": str(USER)}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "missing_tenant"


# --- Gate D-02: /send sellado servicio-a-servicio (X-Internal-Token) --------
@pytest.mark.asyncio
async def test_send_sin_token_interno_401(client: AsyncClient) -> None:
    """Sin X-Internal-Token, /send rechaza (401): cierra el vector de phishing.

    Antes de este sello, cualquier JWT válido reenviado por el gateway podía
    enviar correo arbitrario con la identidad SMTP de la institución.
    """
    response = await client.post(
        "/api/v1/notifications/send",
        json={"recipient_email": "u@x.co", "subject": "S", "body": "B", "tenant_slug": "icetex"},
    )
    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "missing_internal_token"


@pytest.mark.asyncio
async def test_send_token_interno_invalido_403(client: AsyncClient) -> None:
    """Un X-Internal-Token que no casa con el secreto → 403 (comparación constante)."""
    response = await client.post(
        "/api/v1/notifications/send",
        json={"recipient_email": "u@x.co", "subject": "S", "body": "B", "tenant_slug": "icetex"},
        headers={"X-Internal-Token": "token-equivocado"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "invalid_internal_token"


@pytest.mark.asyncio
async def test_get_history_sin_permiso_403(client: AsyncClient, db_conn) -> None:
    """Sin USUA_PERM_ADMIN, el historial tenant-wide devuelve 403 (fail-closed)."""
    db_conn.fetchval.return_value = None  # check_permission → False
    response = await client.get("/api/v1/notifications/history", headers=TENANT_HEADERS)
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_get_notification_sin_permiso_403(client: AsyncClient, db_conn) -> None:
    db_conn.fetchval.return_value = None
    response = await client.get(
        f"/api/v1/notifications/{uuid.uuid4()}", headers=TENANT_HEADERS
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"


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
async def test_health_es_vivacidad_y_no_abre_conexiones(client: AsyncClient) -> None:
    """`/health` es el `healthcheck` del contenedor y no debe tocar nada.

    Abría una conexión SMTP **completa en cada sondeo** —cada 15 segundos contra
    el servidor de correo de la institución— y devolvía 503 si Redis fallaba,
    con lo que el orquestador reiniciaba el servicio: no arregla Redis y sí
    pierde el trabajo en vuelo.
    """
    with patch("app.routers.health.aiosmtplib.SMTP") as smtp:
        response = await client.get("/health")
        assert smtp.call_count == 0, "la vivacidad no abre SMTP"

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "notification-service"}



def _pool_falso():
    """Pool cuyo `acquire()` entrega una conexión que responde al `SELECT 1`."""
    conn = AsyncMock()
    conn.fetchval.return_value = 1
    pool = MagicMock()
    pool.acquire.return_value = AsyncMock(
        __aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock(return_value=False)
    )
    return pool


@pytest.mark.asyncio
async def test_ready_comprueba_las_dependencias(client: AsyncClient) -> None:
    smtp_instance = AsyncMock()
    smtp_instance.connect = AsyncMock()
    smtp_instance.quit = AsyncMock()

    with (
        patch("app.routers.health.aiosmtplib.SMTP", return_value=smtp_instance),
        patch("app.routers.health.get_pool", return_value=_pool_falso()),
    ):
        response = await client.get("/health/ready")

    assert response.status_code == 200
    data = response.json()
    assert data["checks"]["redis"] == "ok"
    assert data["checks"]["smtp"] == "ok"


@pytest.mark.asyncio
async def test_ready_con_smtp_caido_sigue_disponible(client: AsyncClient) -> None:
    """SMTP se informa pero NO decide el 503.

    Un servidor de correo caído no impide encolar notificaciones; sacar el
    servicio del balanceador por eso apagaría también el histórico y los
    webhooks, que sí funcionan.
    """
    with (
        patch("app.routers.health.aiosmtplib.SMTP", side_effect=OSError("sin ruta")),
        patch("app.routers.health.get_pool", return_value=_pool_falso()),
    ):
        response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["checks"]["smtp"] == "error"


@pytest.mark.asyncio
async def test_ready_no_publica_el_servidor_de_correo(client: AsyncClient) -> None:
    """Se reportaba `f"error: {exc}"`, con el nombre y el puerto dentro."""
    with patch(
        "app.routers.health.aiosmtplib.SMTP",
        side_effect=OSError("connect failed: smtp.entidad.gov.co:587"),
    ):
        cuerpo = (await client.get("/health/ready")).text

    assert "smtp.entidad.gov.co" not in cuerpo
    assert "587" not in cuerpo
