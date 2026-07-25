# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de anulación de radicado en dos pasos (paridad legado).

Incluye la emisión del evento `document.radicado.annulled` al aprobar (residual
"purga-en-anulación", E21) — gemelo de los tests de emisión de
`test_documents.py`/`test_reclassify.py`: solo `aprobar` publica; `solicitar` y
`rechazar` no cambian el estado del radicado y no emiten nada.
"""

import json
import uuid
from datetime import UTC, datetime

import pytest

from tests.conftest import TENANT_HEADERS

NOW = datetime.now(tz=UTC)
_USER = str(uuid.uuid4())


def _clearance_row(max_level: int = 3, is_root: bool = False) -> dict:
    """Fila que consume `resolve_caller_clearance` (SELECT is_root, max_level).

    Aprobar/rechazar resuelven el clearance del llamante antes de tocar la
    solicitud (RF-SEG-08), así que consumen UN `fetchrow` previo.
    """
    return {"is_root": is_root, "max_level": max_level}


def _anulacion_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "radicado_id": uuid.uuid4(),
        "estado": "solicitada",
        "causal": "ANU-ERR",
        "motivo": "Error de radicación",
        "solicitado_por": uuid.UUID(_USER),
        "solicitado_at": NOW,
        "decidido_por": None,
        "decidido_at": None,
        "observacion": None,
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_solicitar_anulacion(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    rid = uuid.uuid4()
    mock_conn.fetchval.return_value = "registered"  # radicado_status → vigente
    mock_conn.fetchrow.return_value = _anulacion_row(radicado_id=rid)

    resp = await client.post(
        f"/api/v1/documents/{rid}/anulacion",
        json={"causal": "ANU-ERR", "motivo": "Error de radicación"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 201
    assert resp.json()["estado"] == "solicitada"


@pytest.mark.asyncio
async def test_solicitar_sobre_anulado_409(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = "anulado"
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/anulacion",
        json={"causal": "ANU-DUP"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "ya_anulado"


@pytest.mark.asyncio
async def test_aprobar_anula_el_radicado(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    rid = uuid.uuid4()
    # get_active (solicitada) → decide (UPDATE RETURNING aprobada)
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),  # resolve_caller_clearance
        _anulacion_row(radicado_id=rid, estado="solicitada"),
        _anulacion_row(
            radicado_id=rid, estado="aprobada", decidido_por=uuid.UUID(_USER), decidido_at=NOW
        ),
    ]
    resp = await client.post(
        f"/api/v1/documents/{rid}/anulacion/aprobar",
        json={"observacion": "Procede"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 200
    assert resp.json()["estado"] == "aprobada"


@pytest.mark.asyncio
async def test_aprobar_sin_solicitud_404(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None  # get_active → None
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/anulacion/aprobar",
        json={},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "sin_solicitud_activa"


# ---------------------------------------------------------------------------
# D-06: comprobaciones de autorización (PERM_SOL_ANULAR / PERM_PANU_CODI)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_solicitar_sin_permiso_403(client_with_conn) -> None:
    """check_permission usa fetchval; None → usuario no encontrado → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None  # sin fila en auth_users → no tiene permiso
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/anulacion",
        json={"causal": "ANU-ERR", "motivo": "Sin permiso"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_solicitar_con_permiso_201(client_with_conn) -> None:
    """fetchval truthy → permission ok; fetchrow → INSERT anulacion."""
    client, mock_conn = client_with_conn
    rid = uuid.uuid4()
    mock_conn.fetchval.return_value = True  # permission granted
    mock_conn.fetchrow.return_value = _anulacion_row(radicado_id=rid)
    resp = await client.post(
        f"/api/v1/documents/{rid}/anulacion",
        json={"causal": "ANU-ERR", "motivo": "Con permiso"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 201
    assert resp.json()["estado"] == "solicitada"


@pytest.mark.asyncio
async def test_aprobar_sin_permiso_403(client_with_conn) -> None:
    """PERM_PANU_CODI requerido para aprobar; sin permiso → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/anulacion/aprobar",
        json={},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_rechazar_sin_permiso_403(client_with_conn) -> None:
    """PERM_PANU_CODI requerido para rechazar; sin permiso → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/anulacion/rechazar",
        json={},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


# ---------------------------------------------------------------------------
# Residual "purga-en-anulación" (E21): document.radicado.annulled al aprobar.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aprobar_emite_evento_annulled(client_with_redis) -> None:
    """Aprobar la anulación publica `document.radicado.annulled` en
    orpycamcp.document.events (ADR-010) — gemelo de `_emit_created`.
    knowledge-service lo consume para purgar PERMANENTEMENTE el chunk indexado."""
    client, mock_conn, fake_redis = client_with_redis
    rid = uuid.uuid4()
    tracking = "2026-DEMO-E-000042"
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),  # resolve_caller_clearance
        _anulacion_row(radicado_id=rid, estado="solicitada"),
        _anulacion_row(
            radicado_id=rid, estado="aprobada", decidido_por=uuid.UUID(_USER), decidido_at=NOW
        ),
    ]
    mock_conn.fetchval.return_value = tracking  # get_tracking_number

    resp = await client.post(
        f"/api/v1/documents/{rid}/anulacion/aprobar",
        json={"observacion": "Procede"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 200
    assert fake_redis.xadd.await_count == 1
    stream, fields = fake_redis.xadd.await_args.args
    assert stream == "orpycamcp.document.events"
    assert fields["event_type"] == "document.radicado.annulled"
    assert fields["tenant_slug"] == TENANT_HEADERS["X-Tenant-Slug"]
    assert fields["dedup_key"] == f"{tracking}:annulled"
    payload = json.loads(fields["payload"])
    assert payload == {"id": str(rid), "tracking_number": tracking}


@pytest.mark.asyncio
async def test_rechazar_no_emite_evento(client_with_redis) -> None:
    """Rechazar NO cambia el estado del radicado: no publica ningún evento."""
    client, mock_conn, fake_redis = client_with_redis
    rid = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),  # resolve_caller_clearance
        _anulacion_row(radicado_id=rid, estado="solicitada"),
        _anulacion_row(
            radicado_id=rid, estado="rechazada", decidido_por=uuid.UUID(_USER), decidido_at=NOW
        ),
    ]

    resp = await client.post(
        f"/api/v1/documents/{rid}/anulacion/rechazar",
        json={"observacion": "No procede"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 200
    fake_redis.xadd.assert_not_awaited()


@pytest.mark.asyncio
async def test_solicitar_no_emite_evento(client_with_redis) -> None:
    """Solicitar (paso 1) tampoco cambia el estado del radicado: sin evento."""
    client, mock_conn, fake_redis = client_with_redis
    rid = uuid.uuid4()
    mock_conn.fetchval.return_value = "registered"  # radicado_status → vigente
    mock_conn.fetchrow.return_value = _anulacion_row(radicado_id=rid)

    resp = await client.post(
        f"/api/v1/documents/{rid}/anulacion",
        json={"causal": "ANU-ERR", "motivo": "Error de radicación"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 201
    fake_redis.xadd.assert_not_awaited()


@pytest.mark.asyncio
async def test_aprobar_evento_best_effort_no_revierte_anulacion(client_with_redis) -> None:
    """Si Redis falla al publicar, la anulación NO se revierte (evento best-effort,
    gemelo de `test_create_document_survives_event_failure`)."""
    client, mock_conn, fake_redis = client_with_redis
    fake_redis.xadd.side_effect = RuntimeError("redis down")
    rid = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),  # resolve_caller_clearance
        _anulacion_row(radicado_id=rid, estado="solicitada"),
        _anulacion_row(
            radicado_id=rid, estado="aprobada", decidido_por=uuid.UUID(_USER), decidido_at=NOW
        ),
    ]
    mock_conn.fetchval.return_value = "2026-DEMO-E-000099"

    resp = await client.post(
        f"/api/v1/documents/{rid}/anulacion/aprobar",
        json={"observacion": "Procede"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 200  # la anulación se aplicó pese al fallo de Redis
    assert resp.json()["estado"] == "aprobada"
