# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Traza de intentos DENEGADOS de alterar el ciclo de vida del expediente
(H6, archive_service.py `close_expediente`/`transfer_expediente`).

Hallazgo ALTA del auditor de conformidad que este archivo cubre: el docstring
de `close_expediente` AFIRMABA (Modelo de Requisitos SGDEA) que la pista
registra los intentos denegados, pero `_assert_transition`/`compute_retention`
quedaban FUERA del `try` que emite `archive.expediente_close_failed` — el
intento denegado MÁS FRECUENTE (cerrar un expediente ya 'closed'/'transferred')
no dejaba rastro. Y `transfer_expediente` no tenía NINGÚN asiento de
denegación — invisibilizando justo el control del Acuerdo AGN 001/2024 art.
4.3.2.4 (transferir sin índice firmado).

DB completamente mockeada (`tests/conftest.py`) — la semántica transaccional
real (que la denegación sobreviva/no rompa la cadena de auditoría real) vive
en `tests/integration/test_atomicity.py` (casos (b)/(e) del encargo).
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

import app.services.archive_service as archive_service_module
from tests.conftest import TENANT_HEADERS, make_expediente_record


def _audit_log_calls(mock_conn: AsyncMock) -> list[tuple]:
    return [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]


# ---------------------------------------------------------------------------
# (a) CLOSE denegado por validación (el caso MÁS FRECUENTE): cerrar un
# expediente que ya está 'closed'. Antes del fix, `_assert_transition` corría
# FUERA del `try` -> ningún asiento. Este test debe ponerse ROJO si alguien
# vuelve a sacar la validación del `try`.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_ya_cerrado_audita_denegacion(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    already_closed = make_expediente_record(exp_id=exp_id, exp_status="closed")

    mock_conn.fetchval.return_value = True  # D-08 gate + audit.append prev_hash
    # Único fetchrow: `_expediente_or_404`. `_assert_transition` revienta
    # ANTES de tocar `compute_retention`/la tx -> no hay más fetchrow que
    # consumir.
    mock_conn.fetchrow.side_effect = [already_closed]
    mock_conn.fetch.return_value = []

    resp = await client.post(f"/api/v1/expedientes/{exp_id}/close", headers=TENANT_HEADERS)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "invalid_status_transition"

    audit_calls = _audit_log_calls(mock_conn)
    # args: (sql, tenant, service, actor, canal, action, object_type, object_ref, payload, prev_hash, hash)
    denial = next(a for a in audit_calls if a[5] == "archive.expediente_close_failed")
    assert denial[6] == "expediente"
    assert denial[7] == already_closed["code"]
    payload = json.loads(denial[8])
    assert payload["status_actual"] == "closed"
    assert payload["motivo"] == "invalid_status_transition"
    # Ningún asiento de éxito se coló pese al intento.
    assert not any(a[5] == "archive.expediente_closed" for a in audit_calls)


# ---------------------------------------------------------------------------
# (c) TRANSFER denegado por transición inválida (expediente todavía 'open').
# `_assert_transition` revienta ANTES de `_assert_indice_firmado` -> el motivo
# auditado debe ser 'invalid_status_transition', no 'indice_no_firmado'.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transfer_desde_open_audita_denegacion(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    still_open = make_expediente_record(exp_id=exp_id, exp_status="open")

    mock_conn.fetchval.return_value = True  # D-08 gate + audit.append prev_hash
    # Único fetchrow: `_expediente_or_404`. `_assert_transition` revienta antes
    # de que `_assert_indice_firmado` (get_current del índice) se alcance.
    mock_conn.fetchrow.side_effect = [still_open]
    mock_conn.fetch.return_value = []

    resp = await client.post(f"/api/v1/expedientes/{exp_id}/transfer", headers=TENANT_HEADERS)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "invalid_status_transition"

    audit_calls = _audit_log_calls(mock_conn)
    denial = next(a for a in audit_calls if a[5] == "archive.expediente_transfer_failed")
    assert denial[7] == still_open["code"]
    payload = json.loads(denial[8])
    assert payload["status_actual"] == "open"
    assert payload["motivo"] == "invalid_status_transition"
    assert not any(a[5] == "archive.expediente_transferred" for a in audit_calls)


# ---------------------------------------------------------------------------
# (b, tramo observable con mocks) TRANSFER denegado por índice SIN FIRMAR — el
# control del Acuerdo AGN 001/2024 art. 4.3.2.4. La semántica transaccional
# real (que el asiento sobreviva y la cadena siga íntegra) se prueba en
# integración; aquí solo se observa que el asiento se EMITE con el motivo
# correcto.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transfer_sin_indice_firmado_audita_denegacion(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    closed = make_expediente_record(exp_id=exp_id, exp_status="closed")

    mock_conn.fetchval.return_value = True  # D-08 gate + audit.append prev_hash
    # _expediente_or_404 -> _assert_indice_firmado(get_current) revienta 409
    # antes de la tx.
    mock_conn.fetchrow.side_effect = [
        closed,
        {"id": uuid.uuid4(), "estado": "pendiente_firma"},
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post(f"/api/v1/expedientes/{exp_id}/transfer", headers=TENANT_HEADERS)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "indice_no_firmado"

    audit_calls = _audit_log_calls(mock_conn)
    denial = next(a for a in audit_calls if a[5] == "archive.expediente_transfer_failed")
    assert denial[7] == closed["code"]
    payload = json.loads(denial[8])
    assert payload["status_actual"] == "closed"
    assert payload["motivo"] == "indice_no_firmado"


# ---------------------------------------------------------------------------
# (d) BEST-EFFORT: si el propio `audit.append` del asiento de DENEGACIÓN
# falla, la excepción de negocio ORIGINAL (409) se propaga intacta — nunca se
# degrada a un 500 por un problema de auditoría. Se monkeypatchea el módulo
# `audit` (mismo patrón que `test_patch_revierte_si_falla_el_asiento`), pero
# aquí — a diferencia de ese test — el fallo de `audit.append` NO debe
# propagarse: `close_expediente`/`transfer_expediente` lo atrapan con su
# propio `except Exception` best-effort alrededor del asiento de denegación.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_denial_audit_failure_no_degrada_a_500(
    client_with_conn: tuple[AsyncClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    already_closed = make_expediente_record(exp_id=exp_id, exp_status="closed")

    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [already_closed]
    mock_conn.fetch.return_value = []

    async def _boom(*args: object, **kwargs: object) -> str:
        raise RuntimeError("audit backend down (simulated)")

    monkeypatch.setattr(archive_service_module.audit, "append", _boom)

    resp = await client.post(f"/api/v1/expedientes/{exp_id}/close", headers=TENANT_HEADERS)

    # El 409 real (invalid_status_transition) sobrevive intacto -- NO un 500.
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "invalid_status_transition"


@pytest.mark.asyncio
async def test_transfer_denial_audit_failure_no_degrada_a_500(
    client_with_conn: tuple[AsyncClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    still_open = make_expediente_record(exp_id=exp_id, exp_status="open")

    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [still_open]
    mock_conn.fetch.return_value = []

    async def _boom(*args: object, **kwargs: object) -> str:
        raise RuntimeError("audit backend down (simulated)")

    monkeypatch.setattr(archive_service_module.audit, "append", _boom)

    resp = await client.post(f"/api/v1/expedientes/{exp_id}/transfer", headers=TENANT_HEADERS)

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "invalid_status_transition"
