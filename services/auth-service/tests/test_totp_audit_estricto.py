# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""AREA-04-04: el asiento de las bajas de TOTP es ESTRICTO (sin `except`, sin salida muda)."""

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.services import totp_service
from app.services.totp_service import TotpService


@pytest.mark.asyncio
async def test_sin_auditoria_disponible_la_baja_falla_en_vez_de_callar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(totp_service, "_AUDIT_AVAILABLE", False)
    with pytest.raises(HTTPException) as exc:
        await TotpService(AsyncMock())._audit_strict(
            user_id=uuid.uuid4(), tenant_slug="t", action="totp.desenrolado"
        )
    assert exc.value.status_code == 500
    assert exc.value.detail["error"] == "auditoria_no_disponible"


@pytest.mark.asyncio
async def test_un_fallo_del_asiento_se_propaga(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(totp_service, "_AUDIT_AVAILABLE", True)
    monkeypatch.setattr(totp_service._audit, "append", AsyncMock(side_effect=RuntimeError("x")))
    with pytest.raises(RuntimeError):
        await TotpService(AsyncMock())._audit_strict(
            user_id=uuid.uuid4(), tenant_slug="t", action="totp.restablecido_por_admin"
        )


@pytest.mark.asyncio
async def test_el_evento_corroborante_sigue_siendo_best_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control: `_audit_event` (p. ej. totp.verificado) NO tumba la petición."""
    monkeypatch.setattr(totp_service, "_AUDIT_AVAILABLE", True)
    monkeypatch.setattr(totp_service._audit, "append", AsyncMock(side_effect=RuntimeError("x")))
    await TotpService(AsyncMock())._audit_event(
        user_id=uuid.uuid4(), tenant_slug="t", action="totp.verificado"
    )
