# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests de la traza AGREGADA de consulta (RF-BUS-10/E09; Ley 1712/2014, Decreto
1081/2015) — `ArchiveService.list_expedientes`/`search_expedientes` dejan UNA
entrada `audit.append` por consulta EXITOSA vía `_audit_search_access`.

Mock call sequences (mismo criterio que `test_archive.py`/`test_expediente_search.py`):

LIST con canal auditable (`X-Canal: api` + `X-User-Id`):
  fetchval — [0] D-08 gate, [1] COUNT(*) del listado, [2] audit.append prev_hash
  fetchrow — resolve_caller_clearance (None → PUBLICA)
  execute  — SET search_path (x2) + audit advisory_lock + audit INSERT

SEARCH con canal auditable: igual, salvo que `resolve_caller_clearance` se
resuelve DENTRO de `ArchiveRepository.search_expedientes` (mismo fetchrow).

Sin `X-Canal` (canal='system', default de `_optional_canal`) o sin `user_id`,
`_audit_search_access` no llega a invocar `audit.append` — un fetchval de menos.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from tests.conftest import TENANT_HEADERS, make_expediente_record


def _audit_inserts(mock_conn: AsyncMock, action: str) -> list:
    """Espejo de `test_archive.py::_audit_inserts`: filas INSERT a
    `public.audit_log` cuyo `action` (arg posicional $5, índice 5 tras el SQL)
    coincide con el buscado."""
    return [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str)
        and "INSERT INTO public.audit_log" in c.args[0]
        and c.args[5] == action
    ]


def _hit_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "code": "EXP-2024-0001",
        "name": "Contratación servicios jurídicos",
        "status": "open",
        "opened_at": datetime.now(tz=UTC),
        "rank": 0.4,
    }
    base.update(kw)
    return base


_AUDITABLE_HEADERS = {**TENANT_HEADERS, "X-Canal": "api"}


# ---------------------------------------------------------------------------
# 1. list_expedientes — UNA entrada, con el object_type/ref/action correctos.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_expedientes_audits_aggregate_search_access(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    records = [
        make_expediente_record(exp_id=uuid.uuid4(), code="EXP-2024-0001"),
        make_expediente_record(exp_id=uuid.uuid4(), code="EXP-2024-0002"),
    ]
    mock_conn.fetchrow.return_value = None  # sin membresías → PUBLICA (1)
    # [0]=D-08 gate, [1]=COUNT(*) del listado, [2]=audit.append prev_hash lookup.
    mock_conn.fetchval.side_effect = [True, 2, None]
    mock_conn.fetch.return_value = records

    resp = await client.get("/api/v1/expedientes", headers=_AUDITABLE_HEADERS)

    assert resp.status_code == 200
    calls = _audit_inserts(mock_conn, "archive.expediente_consultado_listado")
    assert len(calls) == 1
    assert calls[0][6] == "expediente_busqueda"  # object_type
    assert calls[0][7] == "listado"  # object_ref
    payload = json.loads(calls[0][8])
    assert payload["total"] == 2
    assert payload["page"] == 1
    assert payload["size"] == 20
    assert payload["status"] is None


# ---------------------------------------------------------------------------
# 2. CRÍTICO — regresión de conformidad: la traza agregada NO debe colarse en
#    el historial-de-acceso por-expediente (`AuditReadRepository`).
# ---------------------------------------------------------------------------


def test_search_object_type_excluded_from_audit_read_object_types() -> None:
    """Si alguien cambiara `_SEARCH_OBJECT_TYPE` a uno de los tipos que lee
    `AuditReadRepository` (`GET /{id}/historial-acceso`, `<pistaAuditoria>` del
    índice firmado), este test debe fallar: sería exactamente el error que
    arruinaría la pista por-expediente, mezclando un evento de CONJUNTO con
    lecturas de UN expediente concreto."""
    from app.repositories.audit_read import _OBJECT_TYPES
    from app.services.archive_service import _SEARCH_OBJECT_REF, _SEARCH_OBJECT_TYPE

    assert _SEARCH_OBJECT_TYPE == "expediente_busqueda"
    assert _SEARCH_OBJECT_REF == "listado"
    assert _SEARCH_OBJECT_TYPE not in _OBJECT_TYPES
    assert "expediente_busqueda" not in _OBJECT_TYPES


# ---------------------------------------------------------------------------
# 3. Gating — canal no auditable / sin user_id no emiten traza.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_expedientes_system_channel_does_not_audit_search_access(
    client_with_conn,
) -> None:
    """Sin `X-Canal` (=> 'system', llamada interna/no proveniente del gateway)
    no se deja traza de consulta agregada — mismo criterio que `_audit_read_access`."""
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None
    mock_conn.fetchval.side_effect = [True, 0]  # [0] gate, [1] COUNT — sin audit
    mock_conn.fetch.return_value = []

    resp = await client.get("/api/v1/expedientes", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    assert _audit_inserts(mock_conn, "archive.expediente_consultado_listado") == []


@pytest.mark.asyncio
async def test_audit_search_access_does_not_write_without_user_id() -> None:
    """`_audit_search_access` con canal de usuario real ('api') pero
    `user_id=None` NO escribe `audit_log` — sin actor real no hay a quién
    atribuir la consulta (mismo gate que `_audit_read_access`). Se prueba el
    método directamente: `require_permission` en el router ya exige un
    `X-User-Id` resoluble, así que este estado solo es alcanzable si
    `ArchiveService` se invoca directamente (otro router, MCP, un futuro
    llamador interno)."""
    from app.services.archive_service import ArchiveService

    mock_conn = AsyncMock()
    svc = ArchiveService(mock_conn, "demo")

    await svc._audit_search_access(
        action="archive.expediente_consultado_listado",
        filtros={"status": None},
        total=0,
        page=1,
        size=20,
        user_id=None,
        canal="api",
    )

    mock_conn.execute.assert_not_called()


# ---------------------------------------------------------------------------
# 4. search_expedientes — el texto crudo de `q` NUNCA llega al payload.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_expedientes_audits_without_leaking_query_text(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None  # sin clearance asignado → PUBLICA
    # [0]=D-08 gate, [1]=COUNT(*) de la búsqueda, [2]=audit.append prev_hash lookup.
    mock_conn.fetchval.side_effect = [True, 1, None]
    mock_conn.fetch.return_value = [_hit_row()]

    resp = await client.get(
        "/api/v1/expedientes/search?q=algo+sensible", headers=_AUDITABLE_HEADERS
    )

    assert resp.status_code == 200
    calls = _audit_inserts(mock_conn, "archive.expediente_consultado_busqueda")
    assert len(calls) == 1
    assert calls[0][6] == "expediente_busqueda"
    assert calls[0][7] == "listado"

    raw_payload_json = calls[0][8]
    assert "algo sensible" not in raw_payload_json

    payload = json.loads(raw_payload_json)
    assert payload["q_presente"] is True
    assert payload["q_len"] == len("algo sensible")
    assert "q" not in payload
    assert payload["total"] == 1


# ---------------------------------------------------------------------------
# 5. Best-effort — un fallo de `audit.append` no rompe la consulta (200).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_expedientes_search_audit_is_best_effort(
    client_with_conn, monkeypatch, caplog
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None
    mock_conn.fetchval.side_effect = [True, 0]  # [0] gate, [1] COUNT
    mock_conn.fetch.return_value = []

    async def _boom(*args, **kwargs):
        raise RuntimeError("audit_log unavailable")

    monkeypatch.setattr("app.services.archive_service.audit.append", _boom)

    with caplog.at_level(logging.WARNING):
        resp = await client.get("/api/v1/expedientes", headers=_AUDITABLE_HEADERS)

    assert resp.status_code == 200
    assert resp.json() == []
    assert any(
        "archive.expediente_consultado_listado" in record.getMessage()
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# 6. NO por-fila — N resultados en la página, UNA sola entrada de auditoría.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_expedientes_audits_once_not_per_row(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    hits = [_hit_row(id=uuid.uuid4(), code=f"EXP-2024-000{i}") for i in range(3)]
    mock_conn.fetchrow.return_value = None
    mock_conn.fetchval.side_effect = [True, 3, None]  # gate, COUNT, prev_hash
    mock_conn.fetch.return_value = hits

    resp = await client.get("/api/v1/expedientes/search?q=juridic", headers=_AUDITABLE_HEADERS)

    assert resp.status_code == 200
    assert resp.json()["total"] == 3
    assert len(resp.json()["hits"]) == 3
    calls = _audit_inserts(mock_conn, "archive.expediente_consultado_busqueda")
    assert len(calls) == 1
