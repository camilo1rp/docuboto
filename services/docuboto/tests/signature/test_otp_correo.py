# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
`signature.core.otp.send_otp_email` (RF-FIR-13, cierre de fuga de OTP).

`notification.api.send` se sustituye: solo se verifica que el envío se marca
``sensible=True``, que el OTP real viaja en ``body`` y que un envío fallido
sube (el `503 canal_2fa_no_disponible` depende de ello). El SMTP y la
redacción de lo persistido los cubre la suite de notification.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from notification.api import NotificationDeliveryError
from signature.core import otp


async def _enviar(**extra) -> None:
    await otp.send_otp_email(
        recipient_email="firmante@example.com",
        tenant_slug="icetex",
        objeto_tipo="solicitud",
        solicitud_id=extra.get("solicitud_id", uuid.uuid4()),
        otp="123456",
        ttl_seconds=300,
    )


@pytest.mark.asyncio
async def test_send_otp_email_marks_payload_sensible_and_keeps_real_otp() -> None:
    with patch("notification.api.send", new=AsyncMock(return_value="n-1")) as send:
        await _enviar()

    send.assert_awaited_once()
    kwargs = send.await_args.kwargs
    # notification debe saber que este envío es sensible para NO persistir su body.
    assert kwargs["sensible"] is True
    # El body sigue llevando el OTP real (inherente al 2FA por correo).
    assert "123456" in kwargs["body"]
    assert kwargs["recipient_email"] == "firmante@example.com"
    assert kwargs["tenant_slug"] == "icetex"


@pytest.mark.asyncio
async def test_send_otp_email_lleva_solo_el_identificador_corto_del_acto() -> None:
    solicitud_id = uuid.uuid4()
    with patch("notification.api.send", new=AsyncMock(return_value="n-1")) as send:
        await _enviar(solicitud_id=solicitud_id)
    body = send.await_args.kwargs["body"]
    assert f"solicitud:{str(solicitud_id).split('-')[0]}" in body
    assert str(solicitud_id) not in body


@pytest.mark.asyncio
async def test_un_correo_no_entregado_sube() -> None:
    """Si el correo no salió, el firmante no puede ver «Enviamos un código»: el
    `503 canal_2fa_no_disponible` solo es alcanzable si este fallo sube."""
    with (
        patch(
            "notification.api.send",
            new=AsyncMock(side_effect=NotificationDeliveryError("smtp caído")),
        ),
        pytest.raises(NotificationDeliveryError),
    ):
        await _enviar()
