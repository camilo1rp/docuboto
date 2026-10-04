# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""AREA-07-03: cuando el sellado del índice se AGOTA, alguien que pueda repararlo se
entera (el asiento `archive.indice_reconcile_agotado` ya existía, pero un asiento no
avisa a nadie)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.clients.notification_client import NotificationDeliveryError
from app.clients.signature_client import SignOutcome
from app.core.config import settings
from app.jobs.indice_reconciliation import _reconciliar_indice

TENANT = "demo"


def _conn(destinatarios: list[dict]) -> AsyncMock:
    conn = AsyncMock()
    conn.execute.return_value = "UPDATE 1"
    conn.fetch.return_value = destinatarios
    conn.fetchrow.return_value = None
    conn.fetchval.return_value = None
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


def _row(intentos: int) -> dict:
    return {
        "id": uuid.uuid4(),
        "expediente_id": uuid.uuid4(),
        "version": 1,
        "xml": "<indice/>",
        "reconcile_intentos": intentos,
        "expediente_code": "EXP-RESERVADO-0099",
    }


@pytest.mark.asyncio
async def test_al_agotarse_se_avisa_a_quien_puede_repararlo(monkeypatch) -> None:
    monkeypatch.setattr(settings, "indice_reconciliation_max_intentos", 3)
    conn = _conn([{"id": uuid.uuid4(), "email": "archivo@entidad.test"}])

    with (
        patch(
            "app.jobs.indice_reconciliation.signature_client.sign_indice_internal",
            new=AsyncMock(return_value=SignOutcome(signed=None, transient=True)),
        ),
        patch("app.jobs.indice_reconciliation.enviar_aviso", new=AsyncMock()) as aviso,
    ):
        resultado = await _reconciliar_indice(conn, TENANT, _row(intentos=2))

    assert resultado == "agotado"
    aviso.assert_awaited_once()
    kwargs = aviso.await_args.kwargs
    assert kwargs["recipient_email"] == "archivo@entidad.test"
    assert kwargs["tenant_slug"] == TENANT
    # el correo sale de la institución: ni código de expediente ni asunto
    assert "EXP-RESERVADO" not in kwargs["subject"] + kwargs["body"]
    # los destinatarios se resuelven con los permisos del endpoint de reintento y el
    # clearance del expediente (en el WHERE, no en una comprobación posterior)
    sql = conn.fetch.await_args.args[0]
    assert "PERM_FIRMA" in sql and "USUA_PERM_EXPEDIENTE" in sql
    assert "e.nivel_seguridad" in sql


@pytest.mark.asyncio
async def test_antes_de_agotarse_no_se_avisa(monkeypatch) -> None:
    """Control: un fallo con backoff pendiente no genera correo (si no, 8 por índice)."""
    monkeypatch.setattr(settings, "indice_reconciliation_max_intentos", 8)
    conn = _conn([{"id": uuid.uuid4(), "email": "archivo@entidad.test"}])

    with (
        patch(
            "app.jobs.indice_reconciliation.signature_client.sign_indice_internal",
            new=AsyncMock(return_value=SignOutcome(signed=None, transient=True)),
        ),
        patch("app.jobs.indice_reconciliation.enviar_aviso", new=AsyncMock()) as aviso,
    ):
        resultado = await _reconciliar_indice(conn, TENANT, _row(intentos=1))

    assert resultado == "fallido"
    aviso.assert_not_awaited()


@pytest.mark.asyncio
async def test_un_correo_que_rebota_no_revierte_el_estado_agotado(monkeypatch) -> None:
    monkeypatch.setattr(settings, "indice_reconciliation_max_intentos", 3)
    conn = _conn(
        [
            {"id": uuid.uuid4(), "email": "a@entidad.test"},
            {"id": uuid.uuid4(), "email": "b@entidad.test"},
        ]
    )
    aviso = AsyncMock(side_effect=[NotificationDeliveryError("rebotó"), None])

    with (
        patch(
            "app.jobs.indice_reconciliation.signature_client.sign_indice_internal",
            new=AsyncMock(return_value=SignOutcome(signed=None, seal_absent=True)),
        ),
        patch("app.jobs.indice_reconciliation.enviar_aviso", new=aviso),
    ):
        resultado = await _reconciliar_indice(conn, TENANT, _row(intentos=2))

    assert resultado == "agotado"
    assert aviso.await_count == 2  # el segundo destinatario se avisó pese al fallo del primero
