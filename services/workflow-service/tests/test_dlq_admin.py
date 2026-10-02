# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del router admin DLQ (ADR-021 Fase 5, PERM_DLQ_ADMIN) — workflow-service.

Espejo de signature-service adaptado al patrón get_db_conn + _actor. Cubre gate de
permiso, resumen sin payload/last_error, gate de clearance (404 neutro), fail-closed
sobre nivel vivo, atomicidad del replay (502 → rollback), concurrencia (409) y
tenant mismatch (422).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
from fastapi import HTTPException, status

from app.core.clearance import (
    assert_can_read_deadletter,
    get_actor,
    resolve_deadletter_level,
    resolve_deadletter_levels,
)
from app.core.constants import DLQ_GROUP
from app.repositories.dead_letter_repository import (
    DEADLETTER_CANDIDATE_CAP,
    DeadLetterRepository,
)
from tests.conftest import TENANT_HEADERS, USER_ID

BASE = "/api/v1/workflow/admin/deadletter"
EVT = "eeeeeeee-0000-0000-0000-000000000001"
NOW = datetime(2026, 6, 29, 10, 0, tzinfo=UTC)
TRACKING = "2024-DEMO-E-000001"
OTHER_GROUP = "signature-service"  # the other consumer sharing evento_dead_letter


def _summary_row(*, payload: dict | None = None) -> dict:
    """Fila candidata tal como la devuelve ahora ``list_scoped_for_clearance``:
    incluye ``payload`` (el router lo usa para resolver el nivel de clearance por
    fila y lo descarta antes de construir el ``DeadLetterSummary``). Por defecto
    trae un payload SIN identificadores de radicado, lo que resuelve a
    CLASIFICADA=3 (fail-closed) vía ``resolve_deadletter_level`` — los tests que
    esperan ver la fila deben otorgar clearance=3 al llamante."""
    return {
        "event_id": EVT,
        "event_type": "document.radicado.created",
        "origin_stream": "orpycamcp.document.events",
        "origin_group": "workflow-service",
        "status": "pending",
        "dead_lettered_at": NOW,
        "resolved_at": None,
        "payload": payload if payload is not None else {"payload": {}},
    }


def _full_row(tenant: str = "demo") -> dict:
    return {
        "event_id": EVT,
        "event_type": "document.radicado.created",
        "correlation_id": "cccc-0001",
        "tenant_slug": tenant,
        "origin_stream": "orpycamcp.document.events",
        "origin_group": "workflow-service",
        "status": "pending",
        "dead_lettered_at": NOW,
        "resolved_at": None,
        "payload": {
            "event_id": EVT,
            "tenant_slug": tenant,
            "payload": {
                "tracking_number": TRACKING,
                "id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
                "doc_type": "E",
            },
        },
        "failure": {"dead_letter_reason": "max_deliveries_exceeded",
                    "dead_letter_last_error": "connection refused"},
    }


# --------------------------------------------------------------------------- list
@pytest.mark.asyncio
async def test_list_requires_perm_dlq_admin(client_with_conn) -> None:
    client, conn, _redis = client_with_conn
    conn.fetchval.return_value = None  # check_permission → denegado
    resp = await client.get(f"{BASE}/", headers=TENANT_HEADERS)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_returns_summaries(client_with_conn) -> None:
    client, conn, _redis = client_with_conn
    conn.fetch.return_value = [_summary_row()]
    with patch(
        "app.routers.admin_deadletter.resolve_caller_clearance",
        AsyncMock(return_value=3),
    ):
        resp = await client.get(f"{BASE}/", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["event_id"] == EVT
    assert resp.headers["X-Total-Count"] == "1"


@pytest.mark.asyncio
async def test_list_no_payload_or_last_error_in_response(client_with_conn) -> None:
    client, conn, _redis = client_with_conn
    conn.fetch.return_value = [_summary_row()]
    with patch(
        "app.routers.admin_deadletter.resolve_caller_clearance",
        AsyncMock(return_value=3),
    ):
        resp = await client.get(f"{BASE}/", headers=TENANT_HEADERS)
    item = resp.json()[0]
    assert "payload" not in item
    assert "tracking_number" not in item
    assert "last_error" not in item
    assert "failure" not in item


# ---------------------------------------------- list: filtro por-fila de clearance
@pytest.mark.asyncio
async def test_list_excludes_row_above_caller_clearance(client_with_conn) -> None:
    """No-read-up en el listado (ADR-021, seguimiento Fase 5): una fila cuyo
    radicado vivo es CLASIFICADA(3) no debe aparecer -ni contar- para un admin
    con clearance insuficiente, igual que el detalle la 404-ea.

    El nivel ahora se resuelve EN LOTE (``resolve_deadletter_levels``, pulido
    Fase 5 "Baja" [2]); se mockea directamente el resultado del batch en vez de
    encadenar ``conn.fetchval`` (que ya no lo consulta la resolución per-row en
    este flujo — ver el test de equivalencia más abajo para la query real)."""
    client, conn, _redis = client_with_conn
    row = _summary_row(payload={"payload": {"tracking_number": TRACKING}})
    conn.fetch.return_value = [row]
    conn.fetchval.return_value = 1  # check_permission (truthy = permiso concedido).
    with (
        patch(
            "app.routers.admin_deadletter.resolve_caller_clearance",
            AsyncMock(return_value=1),
        ),
        patch(
            "app.routers.admin_deadletter.resolve_deadletter_levels",
            AsyncMock(return_value={EVT: 3}),  # nivel_vivo=CLASIFICADA
        ),
    ):
        resp = await client.get(f"{BASE}/", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == []
    assert resp.headers["X-Total-Count"] == "0"


@pytest.mark.asyncio
async def test_list_includes_row_within_caller_clearance(client_with_conn) -> None:
    """Contraparte: un admin con clearance suficiente SÍ ve la fila y el conteo
    la refleja — confirma que el filtro no excluye de más."""
    client, conn, _redis = client_with_conn
    row = _summary_row(payload={"payload": {"tracking_number": TRACKING}})
    conn.fetch.return_value = [row]
    conn.fetchval.return_value = 1  # check_permission (truthy = permiso concedido).
    with (
        patch(
            "app.routers.admin_deadletter.resolve_caller_clearance",
            AsyncMock(return_value=2),
        ),
        patch(
            "app.routers.admin_deadletter.resolve_deadletter_levels",
            AsyncMock(return_value={EVT: 2}),  # nivel_vivo=2 (RESTRINGIDA)
        ),
    ):
        resp = await client.get(f"{BASE}/", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["event_id"] == EVT
    assert resp.headers["X-Total-Count"] == "1"


@pytest.mark.asyncio
async def test_list_filter_fail_closed_on_resolution_error(client_with_conn) -> None:
    """Si resolver el nivel de una fila lanza (payload corrupto, etc.) la fila se
    trata como CLASIFICADA(3) — un admin por debajo de ese clearance no debe verla
    (fail-closed: nunca se deja pasar una fila por error de resolución)."""
    client, conn, _redis = client_with_conn
    row = _summary_row()
    row["payload"] = "not-valid-json{{{"  # fuerza json.loads a lanzar
    conn.fetch.return_value = [row]
    with patch(
        "app.routers.admin_deadletter.resolve_caller_clearance",
        AsyncMock(return_value=2),
    ):
        resp = await client.get(f"{BASE}/", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == []
    assert resp.headers["X-Total-Count"] == "0"


# --------------------------------------------------------- list: cap signaling [1]
@pytest.mark.asyncio
async def test_list_sets_truncated_header_when_cap_reached(client_with_conn) -> None:
    """ADR-021, pulido Fase 5 "Baja" [1]: cuando el conjunto candidato alcanza
    ``DEADLETTER_CANDIDATE_CAP``, el listado deja de ser silencioso — señala el
    truncamiento con el header ``X-Truncated: true`` (además del log.warning, no
    verificable aquí sin capturar logs)."""
    client, conn, _redis = client_with_conn
    conn.fetch.return_value = [_summary_row() for _ in range(DEADLETTER_CANDIDATE_CAP)]
    with (
        patch(
            "app.routers.admin_deadletter.resolve_caller_clearance",
            AsyncMock(return_value=3),
        ),
        patch(
            "app.routers.admin_deadletter.resolve_deadletter_levels",
            AsyncMock(return_value={EVT: 3}),
        ),
    ):
        resp = await client.get(f"{BASE}/", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.headers["X-Truncated"] == "true"


@pytest.mark.asyncio
async def test_list_no_truncated_header_under_cap(client_with_conn) -> None:
    """Contraparte: por debajo del cap el header está AUSENTE (no se emite
    ``X-Truncated: false`` — la ausencia es la señal de "no truncado")."""
    client, conn, _redis = client_with_conn
    conn.fetch.return_value = [_summary_row()]
    with (
        patch(
            "app.routers.admin_deadletter.resolve_caller_clearance",
            AsyncMock(return_value=3),
        ),
        patch(
            "app.routers.admin_deadletter.resolve_deadletter_levels",
            AsyncMock(return_value={EVT: 3}),
        ),
    ):
        resp = await client.get(f"{BASE}/", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert "X-Truncated" not in resp.headers


# ---------------------------------------------------------- list: identidad [3]
@pytest.mark.asyncio
async def test_list_missing_user_id_returns_403(client_with_conn) -> None:
    """ADR-021, pulido Fase 5 "Baja" [3]: el listado unifica su extracción de
    identidad a ``get_actor`` (401 si falta ``X-User-Id``), pero en workflow
    ``require_permission`` es dependencia de ROUTER y se resuelve ANTES —
    ante ``X-User-Id`` ausente, ``check_permission`` recibe ``user_id=None`` y
    deniega con 403 (no 401): la rama 401 de ``get_actor`` queda ensombrecida
    en esta ruta ya protegida (ver docstring de ``get_actor``). No se espera
    401 aquí — ver ``test_get_actor_missing_header_raises_401`` para la rama
    401 real, en aislamiento."""
    client, conn, _redis = client_with_conn
    conn.fetchval.return_value = None  # check_permission → denegado (user_id=None)
    headers = {k: v for k, v in TENANT_HEADERS.items() if k != "X-User-Id"}
    resp = await client.get(f"{BASE}/", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_actor_missing_header_raises_401() -> None:
    """``get_actor`` en aislamiento: sin X-User-Id, 401 explícito (la rama que
    en el listado queda ensombrecida por el 403 de ``require_permission``)."""
    request = MagicMock()
    request.headers = {}
    with pytest.raises(HTTPException) as exc:
        await get_actor(request)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_get_actor_invalid_uuid_raises_401() -> None:
    request = MagicMock()
    request.headers = {"X-User-Id": "not-a-uuid"}
    with pytest.raises(HTTPException) as exc:
        await get_actor(request)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_get_actor_valid_uuid_returns_it() -> None:
    request = MagicMock()
    request.headers = {"X-User-Id": str(USER_ID)}
    assert await get_actor(request) == USER_ID


# ------------------------------------------- resolve_deadletter_levels (batch) [2]
@pytest.mark.asyncio
async def test_resolve_deadletter_levels_equivalent_to_per_row() -> None:
    """Contrato de equivalencia (ADR-021, pulido Fase 5 "Baja" [2]): para cada
    fila, el resultado del batch debe coincidir EXACTAMENTE con el de invocar
    ``resolve_deadletter_level`` fila a fila (capturando cualquier excepción
    como CLASIFICADA, igual que hacía el router antes del batch). Cubre las 5
    ramas fail-closed: hallado sin embebido, hallado con embebido (max),
    no-hallado, sin-identificador, payload corrupto."""
    hallado_id = "eeeeeeee-0000-0000-0000-000000000010"
    hallado_embebido_id = "eeeeeeee-0000-0000-0000-000000000011"
    no_hallado_id = "eeeeeeee-0000-0000-0000-000000000012"
    sin_id_id = "eeeeeeee-0000-0000-0000-000000000013"
    corrupto_id = "eeeeeeee-0000-0000-0000-000000000014"

    rows: dict[str, dict] = {
        hallado_id: {
            "event_id": hallado_id,
            "payload": {"payload": {"tracking_number": "TRK-1"}},
        },
        hallado_embebido_id: {
            "event_id": hallado_embebido_id,
            "payload": {
                "payload": {"tracking_number": "TRK-2", "nivel_nuevo": 3}
            },
        },
        no_hallado_id: {
            "event_id": no_hallado_id,
            "payload": {"payload": {"tracking_number": "TRK-UNKNOWN"}},
        },
        sin_id_id: {
            "event_id": sin_id_id,
            "payload": {"payload": {}},
        },
        corrupto_id: {
            "event_id": corrupto_id,
            "payload": "not-valid-json{{{",
        },
    }

    radicados_by_tracking = {"TRK-1": 2, "TRK-2": 1}  # nivel_seguridad vivo

    def make_conn() -> AsyncMock:
        conn = AsyncMock()

        async def fake_fetch(_sql, idents):
            return [
                {"nivel_seguridad": nivel, "tracking_number": tn, "id": None}
                for tn, nivel in radicados_by_tracking.items()
                if tn in idents
            ]

        conn.fetch = fake_fetch

        async def fake_fetchval(_sql, ident):
            return radicados_by_tracking.get(ident)

        conn.fetchval = fake_fetchval
        return conn

    # --- batch ---
    batch_conn = make_conn()
    batch_levels = await resolve_deadletter_levels(batch_conn, list(rows.values()))

    # --- per-row (mismo fallback fail-closed que usaba el router antes del batch) ---
    per_row_levels: dict[str, int] = {}
    for event_id, row in rows.items():
        conn = make_conn()
        try:
            per_row_levels[event_id] = await resolve_deadletter_level(conn, row)
        except Exception:  # noqa: BLE001
            per_row_levels[event_id] = 3  # CLASIFICADA

    assert batch_levels == per_row_levels
    assert batch_levels[hallado_id] == 2
    assert batch_levels[hallado_embebido_id] == 3  # max(1, nivel_embebido=3)
    assert batch_levels[no_hallado_id] == 3
    assert batch_levels[sin_id_id] == 3
    assert batch_levels[corrupto_id] == 3


@pytest.mark.asyncio
async def test_resolve_deadletter_levels_missing_radicados_table() -> None:
    """Espejo del caso "tabla ausente" del per-row: ``UndefinedTableError`` en
    la consulta en lote deja el mapa vacío → todas las filas parseables caen a
    no-hallado → CLASIFICADA (equivalente al caso de notification-service)."""
    row = {
        "event_id": EVT,
        "payload": {"payload": {"tracking_number": TRACKING}},
    }
    conn = AsyncMock()

    async def raise_undefined_table(*_args, **_kwargs):
        raise asyncpg.UndefinedTableError()

    conn.fetch = raise_undefined_table
    levels = await resolve_deadletter_levels(conn, [row])
    assert levels[EVT] == 3  # CLASIFICADA


# --------------------------------------------------------------------------- detail
@pytest.mark.asyncio
async def test_detail_requires_perm(client_with_conn) -> None:
    client, conn, _redis = client_with_conn
    conn.fetchval.return_value = None
    resp = await client.get(f"{BASE}/{EVT}", headers=TENANT_HEADERS)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_detail_not_found(client_with_conn) -> None:
    client, conn, _redis = client_with_conn
    conn.fetchrow.return_value = None
    resp = await client.get(f"{BASE}/{EVT}", headers=TENANT_HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_detail_clearance_denied(client_with_conn) -> None:
    client, conn, _redis = client_with_conn
    conn.fetchrow.return_value = _full_row()
    denial = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": "not_found", "status": 404},
    )
    mock_audit = AsyncMock()
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=1)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(side_effect=denial)),
        patch("app.routers.admin_deadletter._audit", mock_audit),
        patch("app.routers.admin_deadletter._AUDIT_AVAILABLE", True),
    ):
        resp = await client.get(f"{BASE}/{EVT}", headers=TENANT_HEADERS)
    assert resp.status_code == 404
    # Un GET bloqueado por clearance no debe dejar rastro en audit_log — de lo
    # contrario confirmaría la existencia del objeto clasificado.
    mock_audit.append.assert_not_called()


@pytest.mark.asyncio
async def test_detail_success(client_with_conn) -> None:
    client, conn, _redis = client_with_conn
    conn.fetchrow.return_value = _full_row()
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(return_value=None)),
    ):
        resp = await client.get(f"{BASE}/{EVT}", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["event_id"] == EVT
    assert body["payload"]["tenant_slug"] == "demo"
    assert "failure" in body


@pytest.mark.asyncio
async def test_detail_view_writes_audit_log(client_with_conn) -> None:
    """Un GET autorizado (tras el gate de clearance) deja un asiento
    ``evento.dead_letter.viewed`` en audit_log con el actor humano (ADR-021,
    seguimiento no-bloqueante Fase 5)."""
    client, conn, _redis = client_with_conn
    conn.fetchrow.return_value = _full_row()
    mock_audit = AsyncMock()
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(return_value=None)),
        patch("app.routers.admin_deadletter._audit", mock_audit),
        patch("app.routers.admin_deadletter._AUDIT_AVAILABLE", True),
    ):
        resp = await client.get(f"{BASE}/{EVT}", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    mock_audit.append.assert_called_once()
    call_kwargs = mock_audit.append.call_args.kwargs
    assert call_kwargs["action"] == "evento.dead_letter.viewed"
    assert call_kwargs["object_type"] == "evento"
    assert call_kwargs["object_ref"] == EVT
    assert call_kwargs["actor"] == str(USER_ID)
    assert set(call_kwargs["payload"].keys()) == {
        "previous_status", "event_type", "origin_stream",
    }


# --------------------------------------------------------------------------- replay
@pytest.mark.asyncio
async def test_replay_success(client_with_conn) -> None:
    client, conn, _redis = client_with_conn
    conn.fetchrow.return_value = _full_row()
    conn.execute.return_value = "UPDATE 1"
    mock_audit = AsyncMock()
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(return_value=None)),
        patch("app.routers.admin_deadletter.apply_distribution",
              AsyncMock(return_value="Dirección Jurídica")),
        patch("app.routers.admin_deadletter._audit", mock_audit),
        patch("app.routers.admin_deadletter._AUDIT_AVAILABLE", True),
    ):
        resp = await client.post(f"{BASE}/{EVT}/replay", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["new_status"] == "resolved"
    assert body["previous_status"] == "pending"
    mock_audit.append.assert_called_once()
    assert mock_audit.append.call_args.kwargs["action"] == "evento.dead_letter.replayed"


@pytest.mark.asyncio
async def test_replay_handler_fails_returns_502(client_with_conn) -> None:
    client, conn, _redis = client_with_conn
    conn.fetchrow.return_value = _full_row()
    conn.execute.return_value = "UPDATE 1"
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(return_value=None)),
        patch("app.routers.admin_deadletter.apply_distribution",
              AsyncMock(side_effect=RuntimeError("boom"))),
        patch("app.routers.admin_deadletter._audit", AsyncMock()),
    ):
        resp = await client.post(f"{BASE}/{EVT}/replay", headers=TENANT_HEADERS)
    assert resp.status_code == 502
    body = resp.json()
    # Raw exception must NOT leak to the client (Seguridad #2).
    assert "boom" not in str(body)
    assert body["detail"]["error"] == "replay_failed"
    assert "pendiente" in body["detail"]["detail"]


@pytest.mark.asyncio
async def test_replay_not_pending_returns_409(client_with_conn) -> None:
    client, conn, _redis = client_with_conn
    conn.fetchrow.return_value = _full_row()
    conn.execute.return_value = "UPDATE 0"
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(return_value=None)),
        patch("app.routers.admin_deadletter.apply_distribution",
              AsyncMock(return_value=None)),
        patch("app.routers.admin_deadletter._audit", AsyncMock()),
    ):
        resp = await client.post(f"{BASE}/{EVT}/replay", headers=TENANT_HEADERS)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_replay_tenant_mismatch_returns_422(client_with_conn) -> None:
    client, conn, _redis = client_with_conn
    conn.fetchrow.return_value = _full_row(tenant="otro")
    with patch("app.routers.admin_deadletter._audit", AsyncMock()):
        resp = await client.post(f"{BASE}/{EVT}/replay", headers=TENANT_HEADERS)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_replay_clearance_denied_returns_404(client_with_conn) -> None:
    client, conn, _redis = client_with_conn
    conn.fetchrow.return_value = _full_row()
    denial = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": "not_found", "status": 404},
    )
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=1)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(side_effect=denial)),
        patch("app.routers.admin_deadletter._audit", AsyncMock()),
    ):
        resp = await client.post(f"{BASE}/{EVT}/replay", headers=TENANT_HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_replay_not_found_returns_404(client_with_conn) -> None:
    client, conn, _redis = client_with_conn
    conn.fetchrow.return_value = None
    with patch("app.routers.admin_deadletter._audit", AsyncMock()):
        resp = await client.post(f"{BASE}/{EVT}/replay", headers=TENANT_HEADERS)
    assert resp.status_code == 404


# --------------------------------------------------------------------------- discard
@pytest.mark.asyncio
async def test_discard_success(client_with_conn) -> None:
    client, conn, _redis = client_with_conn
    # get_for_update returns a full row (SELECT *); clearance gate passes.
    conn.fetchrow.return_value = _full_row()
    conn.execute.return_value = "UPDATE 1"
    mock_audit = AsyncMock()
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(return_value=None)),
        patch("app.routers.admin_deadletter._audit", mock_audit),
        patch("app.routers.admin_deadletter._AUDIT_AVAILABLE", True),
    ):
        resp = await client.post(
            f"{BASE}/{EVT}/discard", headers=TENANT_HEADERS, json={"reason": "spurious"}
        )
    assert resp.status_code == 200
    assert resp.json()["new_status"] == "discarded"
    mock_audit.append.assert_called_once()
    assert mock_audit.append.call_args.kwargs["action"] == "evento.dead_letter.discarded"


@pytest.mark.asyncio
async def test_discard_no_reason_returns_422(client_with_conn) -> None:
    client, _conn, _redis = client_with_conn
    resp = await client.post(f"{BASE}/{EVT}/discard", headers=TENANT_HEADERS, json={})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_discard_clearance_denied_returns_404(client_with_conn) -> None:
    """Admin con clearance insuficiente recibe 404 neutro; el estado no transiciona."""
    client, conn, _redis = client_with_conn
    conn.fetchrow.return_value = _full_row()
    denial = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": "not_found", "status": 404},
    )
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=1)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(side_effect=denial)),
        patch("app.routers.admin_deadletter._audit", AsyncMock()),
    ):
        resp = await client.post(
            f"{BASE}/{EVT}/discard", headers=TENANT_HEADERS, json={"reason": "spurious"}
        )
    assert resp.status_code == 404  # neutro, no 403
    # transition_status must NOT have been called → row stays pending.
    # Note: SET search_path is called once by require_permission — that is expected.
    update_calls = [c for c in conn.execute.call_args_list if "UPDATE" in str(c)]
    assert not update_calls, f"transition_status must not be called; found: {update_calls}"


@pytest.mark.asyncio
async def test_discard_not_pending_returns_409(client_with_conn) -> None:
    client, conn, _redis = client_with_conn
    conn.fetchrow.return_value = {
        "event_id": EVT, "status": "resolved",
        "event_type": "x", "origin_stream": "y", "failure": {},
    }
    conn.execute.return_value = "UPDATE 0"
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(return_value=None)),
        patch("app.routers.admin_deadletter._audit", AsyncMock()),
    ):
        resp = await client.post(
            f"{BASE}/{EVT}/discard", headers=TENANT_HEADERS, json={"reason": "late"}
        )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_discard_not_found_returns_404(client_with_conn) -> None:
    client, conn, _redis = client_with_conn
    conn.fetchrow.return_value = None
    resp = await client.post(
        f"{BASE}/{EVT}/discard", headers=TENANT_HEADERS, json={"reason": "x"}
    )
    assert resp.status_code == 404


# ------------------------------------------- origin_group scoping (shared table)
# `evento_dead_letter` is shared with signature-service in the same tenant schema
# (see module note in app/repositories/dead_letter_repository.py). These tests
# cover both ends: (a) the router always queries with ITS OWN origin_group
# (wiring), and (b) the repository methods actually filter rows by
# origin_group so a row belonging to the other consumer is invisible here.

@pytest.mark.asyncio
async def test_list_passes_own_origin_group_to_repo(client_with_conn) -> None:
    client, conn, _redis = client_with_conn
    conn.fetch.return_value = [_summary_row()]
    await client.get(f"{BASE}/", headers=TENANT_HEADERS)
    params = conn.fetch.call_args[0][1:]
    assert DLQ_GROUP in params
    assert OTHER_GROUP not in params


@pytest.mark.asyncio
async def test_detail_passes_own_origin_group_to_repo(client_with_conn) -> None:
    client, conn, _redis = client_with_conn
    conn.fetchrow.return_value = _full_row()
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(return_value=None)),
    ):
        await client.get(f"{BASE}/{EVT}", headers=TENANT_HEADERS)
    # get_by_event_id(event_id, caller_slug, origin_group)
    assert conn.fetchrow.call_args[0][-1] == DLQ_GROUP


@pytest.mark.asyncio
async def test_replay_passes_own_origin_group_to_get_for_update(client_with_conn) -> None:
    client, conn, _redis = client_with_conn
    conn.fetchrow.return_value = _full_row()
    conn.execute.return_value = "UPDATE 1"
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(return_value=None)),
        patch("app.routers.admin_deadletter.apply_distribution",
              AsyncMock(return_value="Dirección Jurídica")),
        patch("app.routers.admin_deadletter._audit", AsyncMock()),
    ):
        await client.post(f"{BASE}/{EVT}/replay", headers=TENANT_HEADERS)
    # get_for_update(event_id, caller_slug, origin_group)
    assert conn.fetchrow.call_args[0][-1] == DLQ_GROUP
    # transition_status(event_id, caller_slug, new_status, origin_group)
    assert conn.execute.call_args[0][-1] == DLQ_GROUP


@pytest.mark.asyncio
async def test_discard_passes_own_origin_group(client_with_conn) -> None:
    client, conn, _redis = client_with_conn
    conn.fetchrow.return_value = _full_row()
    conn.execute.return_value = "UPDATE 1"
    with (
        patch("app.routers.admin_deadletter.resolve_caller_clearance",
              AsyncMock(return_value=3)),
        patch("app.routers.admin_deadletter.assert_can_read_deadletter",
              AsyncMock(return_value=None)),
        patch("app.routers.admin_deadletter._audit", AsyncMock()),
    ):
        await client.post(
            f"{BASE}/{EVT}/discard", headers=TENANT_HEADERS, json={"reason": "x"}
        )
    assert conn.fetchrow.call_args[0][-1] == DLQ_GROUP
    assert conn.execute.call_args[0][-1] == DLQ_GROUP


@pytest.mark.asyncio
async def test_repo_get_by_event_id_filters_by_origin_group() -> None:
    """Repository-level: a row for the same event_id/tenant but a DIFFERENT
    origin_group (the other consumer's row in the shared table) must not be
    visible — this is the actual filtering behaviour, not just call wiring."""
    table = {
        (EVT, "demo", DLQ_GROUP): {"event_id": EVT, "origin_group": DLQ_GROUP},
        (EVT, "demo", OTHER_GROUP): {"event_id": EVT, "origin_group": OTHER_GROUP},
    }

    async def fake_fetchrow(_sql, event_id, caller_slug, origin_group):
        return table.get((event_id, caller_slug, origin_group))

    conn = AsyncMock()
    conn.fetchrow = fake_fetchrow
    repo = DeadLetterRepository(conn)

    own_row = await repo.get_by_event_id(EVT, "demo", DLQ_GROUP)
    assert own_row is not None and own_row["origin_group"] == DLQ_GROUP

    # Same event_id/tenant, but asking under the OTHER service's group should
    # not leak this service's row, nor return the other service's row from here.
    cross_row = await repo.get_by_event_id(EVT, "demo", OTHER_GROUP)
    assert cross_row is not None and cross_row["origin_group"] == OTHER_GROUP
    assert cross_row != own_row


@pytest.mark.asyncio
async def test_repo_transition_status_does_not_affect_other_origin_group() -> None:
    """transition_status's UPDATE … WHERE origin_group = $N must report 0 rows
    affected when the row id matches but the origin_group belongs to the other
    consumer — replay/discard must not silently resolve someone else's event."""
    conn = AsyncMock()

    async def fake_execute(_sql, event_id, caller_slug, new_status, origin_group):
        if origin_group != DLQ_GROUP:
            return "UPDATE 0"
        return "UPDATE 1"

    conn.execute = fake_execute
    repo = DeadLetterRepository(conn)

    rows_own = await repo.transition_status(EVT, "demo", "resolved", DLQ_GROUP)
    assert rows_own == 1

    rows_other = await repo.transition_status(EVT, "demo", "resolved", OTHER_GROUP)
    assert rows_other == 0


# --------------------------------------------------- assert_can_read_deadletter (unit)
@pytest.mark.asyncio
async def test_fail_closed_nivel_radicado_not_found() -> None:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)
    row = {"payload": {"payload": {"tracking_number": TRACKING}}}
    with pytest.raises(HTTPException) as exc:
        await assert_can_read_deadletter(conn, caller_clearance=1, row=row)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_fail_closed_nivel_max_clearance_passes() -> None:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)
    row = {"payload": {"payload": {"tracking_number": TRACKING}}}
    await assert_can_read_deadletter(conn, caller_clearance=3, row=row)


# ------------------------------------------- repo.list_scoped_for_clearance (unit)
@pytest.mark.asyncio
async def test_repo_list_scoped_for_clearance_includes_payload_and_caps() -> None:
    """A diferencia de list_pending, list_scoped_for_clearance debe (a) incluir
    ``payload`` en la proyección (el router lo necesita para resolver el nivel de
    clearance por fila) y (b) pasar el ``cap`` de seguridad como LIMIT en SQL
    (no paginación de usuario: la paginación ocurre en app tras el filtro)."""
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    repo = DeadLetterRepository(conn)

    await repo.list_scoped_for_clearance("demo", DLQ_GROUP, status="pending", cap=1000)

    sql, *params = conn.fetch.call_args[0]
    assert "payload" in sql
    assert "LIMIT" in sql
    assert "OFFSET" not in sql  # sin paginación SQL — se pagina en app tras filtrar
    assert params == ["demo", "pending", DLQ_GROUP, 1000]


@pytest.mark.asyncio
async def test_repo_list_scoped_for_clearance_filters_origin_stream() -> None:
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    repo = DeadLetterRepository(conn)

    await repo.list_scoped_for_clearance(
        "demo", DLQ_GROUP, status="pending", origin_stream="orpycamcp.document.events"
    )

    sql, *params = conn.fetch.call_args[0]
    assert "origin_stream" in sql
    assert "orpycamcp.document.events" in params


def test_las_rutas_del_dlq_toman_la_identidad_de_get_actor() -> None:
    """AREA-01-07: detalle, replay y discard dejaron el `_actor` opcional. Ante un
    X-User-Id ausente el gate de router da 403 antes, así que la rama 401 no se
    observa por HTTP: se comprueba el cableado de la dependencia."""
    from app.routers import admin_deadletter as mod

    assert not hasattr(mod, "_actor")
    for ruta in mod.router.routes:
        if ruta.path.endswith(("/{event_id}", "/{event_id}/replay", "/{event_id}/discard")):
            llamadas = [d.call for d in ruta.dependant.dependencies]
            assert get_actor in llamadas, ruta.path
