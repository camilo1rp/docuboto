# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Unit tests — app.core.http_clients.send_otp_email (RF-FIR-13, cierre de fuga
de OTP, seguimiento de seguridad de E06 Inc.2).

httpx.AsyncClient se mockea (sin red real, sin notification-service real):
solo se verifica que el payload que este cliente construye marca
``sensible=True`` y que el OTP real viaja en ``body`` (el envío SMTP en sí
es responsabilidad de notification-service, cubierto en su propia suite).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core import http_clients


def _mock_async_client(status_code: int = 202) -> MagicMock:
    """Sustituye httpx.AsyncClient(...) por un context manager mockeado."""
    fake_response = MagicMock()
    fake_response.status_code = status_code
    fake_response.text = ""

    inner_client = AsyncMock()
    inner_client.post = AsyncMock(return_value=fake_response)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=inner_client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


@pytest.mark.asyncio
async def test_send_otp_email_marks_payload_sensible_and_keeps_real_otp() -> None:
    cm = _mock_async_client()
    solicitud_id = uuid.uuid4()

    with patch("app.core.http_clients.httpx.AsyncClient", return_value=cm):
        await http_clients.send_otp_email(
            recipient_email="firmante@example.com",
            tenant_slug="icetex",
            objeto_tipo="solicitud",
            solicitud_id=solicitud_id,
            otp="123456",
            ttl_seconds=300,
        )

    inner_client = cm.__aenter__.return_value
    inner_client.post.assert_awaited_once()
    _, kwargs = inner_client.post.call_args
    sent_payload = kwargs["json"]

    # Cierre de la fuga: notification-service debe saber que este envío es
    # sensible (RF-FIR-13) para NO persistir su body.
    assert sent_payload["sensible"] is True
    # El body enviado por SMTP sigue llevando el OTP real (inherente al 2FA
    # por correo) — la redacción ocurre del lado de notification-service.
    assert "123456" in sent_payload["body"]
    assert sent_payload["recipient_email"] == "firmante@example.com"
    assert sent_payload["tenant_slug"] == "icetex"


@pytest.mark.asyncio
async def test_send_otp_email_raises_on_non_2xx_status() -> None:
    cm = _mock_async_client(status_code=500)

    with (
        patch("app.core.http_clients.httpx.AsyncClient", return_value=cm),
        pytest.raises(http_clients.OtpEmailDeliveryError),
    ):
        await http_clients.send_otp_email(
            recipient_email="firmante@example.com",
            tenant_slug="icetex",
            objeto_tipo="solicitud",
            solicitud_id=uuid.uuid4(),
            otp="654321",
            ttl_seconds=300,
        )


@pytest.mark.asyncio
async def test_send_otp_email_raises_on_502_envio_fallido() -> None:
    """`502` es la respuesta de notification-service cuando el SMTP no entregó.

    Antes esa ruta contestaba `202 Accepted` pasara lo que pasara: agotaba sus
    tres reintentos, guardaba la notificación como `failed` y respondía que sí.
    Con eso, el `503 canal_2fa_no_disponible` que este servicio tiene escrito
    **no podía dispararse nunca** por un correo no entregado — el firmante veía
    «Enviamos un código, vence en 4:48» esperando un correo que nadie mandó.
    Un fail-closed inalcanzable es peor que no tenerlo: se lee como garantía.

    Se encontró recorriendo el sistema con MailHog apagado. La fila del
    historial lo decía —`status=failed, attempts=3`— y la pantalla decía lo
    contrario.
    """
    cm = _mock_async_client(status_code=502)
    with (
        patch("app.core.http_clients.httpx.AsyncClient", return_value=cm),
        pytest.raises(http_clients.OtpEmailDeliveryError),
    ):
        await http_clients.send_otp_email(
            recipient_email="u@x.co",
            tenant_slug="demo",
            objeto_tipo="borrador",
            solicitud_id=uuid.uuid4(),
            otp="123456",
            ttl_seconds=300,
        )
