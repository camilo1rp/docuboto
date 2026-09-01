# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Smoke tests — cadena de firma (E06, ADR-016).

These tests exercise the HTTP layer with mocked DB connections.
Exhaustive integration tests are delegated to the testing-engineer.

Auth model change (RBAC identity fix):
  - All routes now require PERM_FIRMA via require_permission (DB-based, fail-closed).
  - Actor is read from X-User-Id header (internal auth_users.id UUID, NOT keycloak_sub).
  - The old get_current_user (JWKS/JWT) dependency is gone.
  - conn.fetchval call #1 in each request is the check_permission query (returns True
    for happy-path tests via _make_conn default). Business-logic fetchval calls follow.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.database import get_tenant_conn
from app.main import app
from tests.conftest import TENANT_HEADERS, USER

# Fixture IDs
SOL_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
TURNO_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
FIRMANTE2 = uuid.UUID("22222222-2222-2222-2222-222222222222")
FIRMA_ID = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
NOW = datetime.now(tz=UTC)


def _sol_row(estado: str = "pendiente", nivel_seguridad: int = 1) -> dict:
    return {
        "id": SOL_ID,
        "objeto_tipo": "radicado",
        "objeto_id": "2024-DEMO-E-000001",
        "titulo": "Oficio de prueba",
        "payload_sha256": None,
        "estado": estado,
        "creado_por": USER,
        "nivel_seguridad": nivel_seguridad,
        "created_at": NOW,
        "updated_at": NOW,
    }


def _turno_row(
    orden: int = 1,
    firmante_id: uuid.UUID = USER,
    estado: str = "pendiente",
    firma_id: uuid.UUID | None = None,
) -> dict:
    return {
        "id": TURNO_ID,
        "solicitud_id": SOL_ID,
        "orden": orden,
        "firmante_id": firmante_id,
        "estado": estado,
        "firma_id": firma_id,
        "comentario": None,
        "decidido_at": None,
    }


def _exec_sig(query, *args, **kwargs):
    """side_effect robusto para conn.execute.

    Devuelve el command tag 'UPDATE 1' solo para el UPDATE de firma_cadena
    (que marcar_turno parsea), y None para el resto de execute (advisory lock
    de audit.append, INSERT de audit_log, UPDATE de firma_solicitud). Inmune al
    número exacto de execute internos de audit.append.
    """
    if "UPDATE firma_cadena" in query:
        return "UPDATE 1"
    return None


def _firma_row() -> dict:
    return {
        "id": FIRMA_ID,
        "objeto_tipo": "radicado",
        "objeto_id": "2024-DEMO-E-000001",
        "formato_solicitado": None,
        "firmante_id": USER,
        "payload_sha256": "a" * 64,
        "firma_blob": "b" * 64,
        "provider": "nativa",
        "sello_tiempo": NOW,
        "created_at": NOW,
    }


def _make_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.execute.return_value = None
    conn.fetchrow.return_value = None
    # Default True: first fetchval is check_permission (require_permission dep).
    # Tests with specific business-logic fetchval needs use side_effect.
    conn.fetchval.return_value = True
    conn.fetch.return_value = []
    tx = AsyncMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx)
    return conn


def _override(conn: AsyncMock) -> None:
    """Override get_tenant_conn only — auth now comes from X-User-Id in TENANT_HEADERS."""
    async def _conn_dep():
        yield conn

    app.dependency_overrides[get_tenant_conn] = _conn_dep


def _clear() -> None:
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /requests — create chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_request_happy_path() -> None:
    conn = _make_conn()
    sol = _sol_row()
    turno = _turno_row()

    # fetchval order: [check_permission(True), resolver_nivel_por_objeto(1=PUBLICA),
    #                  existe_abierta(None=False), audit.append→prev_hash(None)]
    # El nivel lo devuelve la tabla `radicados`, NO el body: si no resolviera, la
    # creación sería 404 (fail-closed), no una cadena de nivel 1 por defecto.
    conn.fetchval.side_effect = [True, 1, None, None]
    conn.fetchrow.side_effect = [
        {},    # resolve_caller_clearance → PUBLICA=1 (>= nivel 1), creation gate passes
        sol,   # crear_solicitud RETURNING
    ]
    conn.fetch.return_value = [turno]  # list_turnos

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/signature/requests",
                json={
                    "objeto_tipo": "radicado",
                    "objeto_id": "2024-DEMO-E-000001",
                    "titulo": "Oficio de prueba",
                    "firmantes": [str(USER)],
                },
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["objeto_tipo"] == "radicado"
        assert body["estado"] == "pendiente"
        assert body["nivel_seguridad"] == 1
        assert len(body["items"]) == 1
        assert body["items"][0]["firmante_id"] == str(USER)
    finally:
        _clear()


@pytest.mark.asyncio
async def test_create_request_nivel3_clearance1_returns_404() -> None:
    """No-read-up on creation (D-15 #1): clearance-1 actor creating a chain on a
    nivel-3 radicado gets a neutral 404 (no existence/level leak), before existe_abierta."""
    conn = _make_conn()
    # fetchval: [check_permission(True), resolver_nivel_por_objeto(3)]
    conn.fetchval.side_effect = [True, 3]
    conn.fetchrow.side_effect = [{}]  # resolve_caller_clearance → PUBLICA=1 < nivel 3

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/signature/requests",
                json={
                    "objeto_tipo": "radicado",
                    "objeto_id": "2024-DEMO-S-CLASIF",
                    "firmantes": [str(USER)],
                },
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "solicitud_not_found"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_create_request_conflict_when_open_chain_exists() -> None:
    conn = _make_conn()
    # fetchval: [check_permission(True), resolver_nivel_por_objeto(1=PUBLICA), existe_abierta(1=True)]
    conn.fetchval.side_effect = [True, 1, 1]
    conn.fetchrow.side_effect = [{}]  # resolve_caller_clearance → PUBLICA=1 (gate passes)

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/signature/requests",
                json={
                    "objeto_tipo": "radicado",
                    "objeto_id": "2024-DEMO-E-000001",
                    "firmantes": [str(USER)],
                },
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "cadena_existente"
    finally:
        _clear()


# ---------------------------------------------------------------------------
# GET /requests/{id} — get by id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_request_not_found() -> None:
    conn = _make_conn()
    conn.fetchrow.return_value = None  # get_solicitud → 404
    # fetchval.return_value = True (default) handles check_permission

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/signature/requests/{SOL_ID}",
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "solicitud_not_found"
    finally:
        _clear()


# ---------------------------------------------------------------------------
# GET /pending — inbox
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_pending_empty() -> None:
    conn = _make_conn()
    conn.fetch.return_value = []  # no pending items
    # fetchval.return_value = True (default) handles check_permission

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/signature/pending", headers=TENANT_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0
        assert body["page"] == 1
    finally:
        _clear()


# ---------------------------------------------------------------------------
# POST /requests/{id}/sign — firmar
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sign_turn_not_member_returns_403() -> None:
    conn = _make_conn()
    sol = _sol_row()
    conn.fetchrow.return_value = sol   # get_solicitud
    # fetchval: [check_permission(True), es_firmante(None=False)]
    conn.fetchval.side_effect = [True, None]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "no_es_firmante"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_sign_turn_wrong_turn_returns_403() -> None:
    conn = _make_conn()
    sol = _sol_row()
    # turno with a different firmante_id (actor USER is in chain but not their turn)
    turno_other = _turno_row(firmante_id=FIRMANTE2)

    conn.fetchrow.side_effect = [
        sol,         # get_or_404
        {},          # resolve_caller_clearance (_assert_can_read) → clearance=1, nivel=1 → OK
        turno_other, # turno_activo
    ]
    # fetchval: [check_permission(True), es_firmante(1=True)]
    conn.fetchval.side_effect = [True, 1]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "no_es_su_turno"
    finally:
        _clear()


# ---------------------------------------------------------------------------
# POST /requests/{id}/reject — rechazar
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_solicitud_already_closed_returns_409() -> None:
    conn = _make_conn()
    sol_firmado = _sol_row(estado="firmado")
    conn.fetchrow.return_value = sol_firmado
    # fetchval.return_value = True (default) → check_permission passes.
    # Estado guard fires before es_firmante; es_firmante fetchval must NOT be called.

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/reject",
                json={"motivo": "Documento incorrecto"},
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "solicitud_no_pendiente"
        # check_permission called once (fetchval #1).
        # es_firmante must NOT have been reached → total fetchval calls == 1.
        assert conn.fetchval.call_count == 1
    finally:
        _clear()


# ---------------------------------------------------------------------------
# POST /batch — firmar_lote
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_sign_partial_failure() -> None:
    """Batch continues even if one item fails; omitted items report the error.

    Both items fail BEFORE the 2FA gate (es_firmante / get_or_404), so the
    outcome is independent of settings.firma_2fa_required. The payload still
    carries a syntactically valid challenge_id/otp per item — LoteItem requires
    them structurally regardless of the config flag (BREAKING contract, Q2).
    """
    conn = _make_conn()
    sol = _sol_row()

    # First solicitud: actor is not in chain → 403 no_es_firmante
    # Second solicitud: same (simulate not found → 404)
    sid1 = uuid.uuid4()
    sid2 = uuid.uuid4()

    # get_solicitud returns sol for first call, None for second (not found).
    # sid1: _assert_can_read fires after get_or_404 (clearance row → clearance=1 → OK).
    # sid2: get_or_404 returns None → 404 before _assert_can_read.
    conn.fetchrow.side_effect = [
        sol,   # sid1: get_or_404
        {},    # sid1: resolve_caller_clearance (_assert_can_read) → clearance=1, nivel=1 → OK
        None,  # sid2: get_or_404 → 404
    ]
    # fetchval: [check_permission(True), es_firmante_sid1(None=False)]
    conn.fetchval.side_effect = [True, None]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/signature/batch",
                json={
                    "items": [
                        {"solicitud_id": str(sid1), "challenge_id": str(uuid.uuid4()), "otp": "000000"},
                        {"solicitud_id": str(sid2), "challenge_id": str(uuid.uuid4()), "otp": "000000"},
                    ]
                },
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 200
        results = resp.json()["resultados"]
        assert len(results) == 2
        assert all(r["resultado"] == "omitido" for r in results)
    finally:
        _clear()


# ---------------------------------------------------------------------------
# Schema validation — SolicitudCreate
# ---------------------------------------------------------------------------


def test_solicitud_create_rejects_invalid_sha256() -> None:
    from pydantic import ValidationError

    from app.schemas.cadena import SolicitudCreate

    with pytest.raises(ValidationError):
        SolicitudCreate(
            objeto_tipo="radicado",
            objeto_id="x",
            payload_sha256="NOTHEXA",  # not 64 hex chars
            firmantes=[USER],
        )


def test_solicitud_create_accepts_valid_sha256() -> None:
    from app.schemas.cadena import SolicitudCreate

    s = SolicitudCreate(
        objeto_tipo="radicado",
        objeto_id="x",
        payload_sha256="a" * 64,
        firmantes=[USER],
    )
    assert s.payload_sha256 == "a" * 64


def test_solicitud_create_rejects_empty_firmantes() -> None:
    from pydantic import ValidationError

    from app.schemas.cadena import SolicitudCreate

    with pytest.raises(ValidationError):
        SolicitudCreate(objeto_tipo="radicado", objeto_id="x", firmantes=[])


# ===========================================================================
# EXTENDED COVERAGE — testing-engineer (E06, RF-FIR-04/11/12/14)
# ===========================================================================

# ---------------------------------------------------------------------------
# Schema — FirmarRequest body cannot inject firmante_id (RF-FIR-11)
# ---------------------------------------------------------------------------


def test_firmar_request_schema_ignores_firmante_id() -> None:
    """FirmarRequest has no firmante_id field — body cannot inject actor (RF-FIR-11)."""
    from app.schemas.cadena import FirmarRequest

    req = FirmarRequest(payload_xml="<x/>", firmante_id=str(uuid.uuid4()))  # type: ignore[call-arg]
    assert not hasattr(req, "firmante_id")


# ---------------------------------------------------------------------------
# POST /requests — multi-firmante ordering (orden 1..N)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_request_multi_firmantes_ordered() -> None:
    """Items appear in the same order firmantes were supplied (orden 1..N)."""
    conn = _make_conn()
    sol = _sol_row()
    turno1 = _turno_row(orden=1, firmante_id=USER)
    turno2 = _turno_row(orden=2, firmante_id=FIRMANTE2)

    # fetchval order: [check_permission, resolver_nivel_por_objeto(1), existe_abierta(None), audit_prev(None)]
    conn.fetchval.side_effect = [True, 1, None, None]
    conn.fetchrow.side_effect = [
        {},   # resolve_caller_clearance → PUBLICA=1 (gate passes)
        sol,  # crear_solicitud RETURNING
    ]
    conn.fetch.return_value = [turno1, turno2]  # list_turnos

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/signature/requests",
                json={
                    "objeto_tipo": "radicado",
                    "objeto_id": "2024-DEMO-E-000001",
                    "firmantes": [str(USER), str(FIRMANTE2)],
                },
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 201
        body = resp.json()
        items = body["items"]
        assert len(items) == 2
        assert items[0]["orden"] == 1
        assert items[0]["firmante_id"] == str(USER)
        assert items[1]["orden"] == 2
        assert items[1]["firmante_id"] == str(FIRMANTE2)
    finally:
        _clear()


# ---------------------------------------------------------------------------
# GET /requests/{id} — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_request_by_id_happy_path() -> None:
    """GET /requests/{id} returns full chain: header + items."""
    conn = _make_conn()
    sol = _sol_row()
    turno = _turno_row()

    conn.fetchrow.return_value = sol
    conn.fetch.return_value = [turno]
    # fetchval.return_value = True (default) handles check_permission

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/signature/requests/{SOL_ID}",
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["solicitud_id"] == str(SOL_ID)
        assert body["estado"] == "pendiente"
        assert len(body["items"]) == 1
    finally:
        _clear()


# ---------------------------------------------------------------------------
# GET /requests?objeto_tipo=&objeto_id= — by object (open chain)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_request_by_object_returns_open_chain() -> None:
    """GET /requests?objeto_tipo=&objeto_id= returns the open chain for an object."""
    conn = _make_conn()
    sol = _sol_row()
    turno = _turno_row()

    conn.fetchrow.return_value = sol
    conn.fetch.return_value = [turno]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(
                "/api/v1/signature/requests",
                params={"objeto_tipo": "radicado", "objeto_id": "2024-DEMO-E-000001"},
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["objeto_tipo"] == "radicado"
        assert body["estado"] == "pendiente"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_get_request_by_object_not_found() -> None:
    """GET /requests?… → 404 cadena_not_found when no open chain exists."""
    conn = _make_conn()
    conn.fetchrow.return_value = None

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(
                "/api/v1/signature/requests",
                params={"objeto_tipo": "radicado", "objeto_id": "DOES-NOT-EXIST"},
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "cadena_not_found"
    finally:
        _clear()


# ---------------------------------------------------------------------------
# GET /pending — inbox: items present + X-Total-Count header
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_pending_with_items_and_x_total_count() -> None:
    """Inbox returns PendienteItem rows and sets X-Total-Count from total_count column."""
    conn = _make_conn()
    pending_row = {
        "solicitud_id": SOL_ID,
        "objeto_tipo": "radicado",
        "objeto_id": "2024-DEMO-E-000001",
        "titulo": "Oficio de prueba",
        "orden": 1,
        "created_at": NOW,
        "total_count": 3,
    }
    conn.fetch.return_value = [pending_row]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(
                "/api/v1/signature/pending",
                params={"page": "1", "size": "20"},
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 3
        assert body["page"] == 1
        assert len(body["items"]) == 1
        assert body["items"][0]["solicitud_id"] == str(SOL_ID)
        assert resp.headers["x-total-count"] == "3"
    finally:
        _clear()


# ---------------------------------------------------------------------------
# POST /sign — happy path (intermediate turn, solicitud stays pendiente)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sign_happy_path_not_last_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Signing an intermediate turn updates that turn to 'firmado' but the solicitud
    remains 'pendiente' because there is a next pending turn.

    2FA disabled (E06 Inc.2, RF-FIR-13): this test targets the turn-order state
    machine, not the OTP gate — see test_cadena_2fa.py for challenge/OTP coverage.
    """
    monkeypatch.setattr(settings, "firma_2fa_required", False)
    conn = _make_conn()
    sol = _sol_row()
    turno1 = _turno_row(orden=1, firmante_id=USER)
    turno2 = _turno_row(orden=2, firmante_id=FIRMANTE2)
    firma_ins = _firma_row()

    # fetchval order: check_permission(True) | es_firmante(1) | audit.append→prev_hash(None)
    conn.fetchval.side_effect = [True, 1, None]
    conn.fetchrow.side_effect = [
        sol,       # get_or_404
        {},        # resolve_caller_clearance (firmar._assert_can_read) → clearance=1 → OK
        turno1,    # turno_activo (early, non-locking)
        turno1,    # turno_activo_for_update (inside txn, row-lock)
        firma_ins, # FirmaRepository.insert
        turno2,    # next_active → not None → solicitud stays pendiente
        sol,       # get() get_solicitud
        {},        # resolve_caller_clearance (get()._assert_can_read) → clearance=1 → OK
    ]
    conn.fetch.return_value = [turno1, turno2]  # list_turnos
    conn.execute.side_effect = _exec_sig  # 'UPDATE 1' para firma_cadena, None resto

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["estado"] == "pendiente"  # chain still open
        # actualizar_estado_solicitud (UPDATE firma_solicitud) must NOT have been called
        firma_sol_updates = [
            c for c in conn.execute.call_args_list
            if "firma_solicitud" in str(c.args[0])
        ]
        assert len(firma_sol_updates) == 0
    finally:
        _clear()


# ---------------------------------------------------------------------------
# POST /sign — last turn marks solicitud 'firmado' (RF-FIR-14 atomicity)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sign_last_turn_marks_solicitud_firmado(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    When the final signer acts, solicitud.estado advances to 'firmado'.
    Verifies that actualizar_estado_solicitud is called (execute count == 2).

    2FA disabled (E06 Inc.2, RF-FIR-13) — see test_cadena_2fa.py for OTP coverage.
    """
    monkeypatch.setattr(settings, "firma_2fa_required", False)
    conn = _make_conn()
    sol_pendiente = _sol_row(estado="pendiente")
    sol_firmado = _sol_row(estado="firmado")
    turno = _turno_row(orden=1, firmante_id=USER, estado="pendiente")
    turno_firmado = _turno_row(orden=1, firmante_id=USER, estado="firmado", firma_id=FIRMA_ID)
    firma_ins = _firma_row()

    # fetchval: check_permission(True) | es_firmante(1) | audit.append→prev_hash(None)
    conn.fetchval.side_effect = [True, 1, None]
    conn.fetchrow.side_effect = [
        sol_pendiente,  # get_or_404
        {},             # resolve_caller_clearance (firmar._assert_can_read) → clearance=1 → OK
        turno,          # turno_activo (early, non-locking)
        turno,          # turno_activo_for_update (inside txn, row-lock)
        firma_ins,      # FirmaRepository.insert
        None,           # next_active → None → triggers actualizar_estado_solicitud
        sol_firmado,    # get() get_solicitud (updated)
        {},             # resolve_caller_clearance (get()._assert_can_read) → clearance=1 → OK
    ]
    conn.fetch.return_value = [turno_firmado]  # list_turnos
    conn.execute.side_effect = _exec_sig  # 'UPDATE 1' para firma_cadena, None resto

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["estado"] == "firmado"
        # actualizar_estado_solicitud (UPDATE firma_solicitud SET estado … 'firmado') must be called
        firma_sol_updates = [
            c for c in conn.execute.call_args_list
            if "firma_solicitud" in str(c.args[0])
        ]
        assert len(firma_sol_updates) == 1
        assert firma_sol_updates[0].args[2] == "firmado"
    finally:
        _clear()


# ---------------------------------------------------------------------------
# POST /sign — solicitud not pending → 409 (estado guard before es_firmante)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sign_solicitud_already_closed_returns_409() -> None:
    """
    Attempt to sign a solicitud that is already 'firmado' → 409.
    Estado guard fires before es_firmante (es_firmante fetchval must NOT be called).
    """
    conn = _make_conn()
    sol_firmado = _sol_row(estado="firmado")
    conn.fetchrow.return_value = sol_firmado
    # fetchval.return_value = True (default) → check_permission passes (call #1).

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "solicitud_no_pendiente"
        # check_permission called once (fetchval #1); es_firmante must NOT be called.
        assert conn.fetchval.call_count == 1
    finally:
        _clear()


# ---------------------------------------------------------------------------
# POST /reject — happy path marks solicitud 'rechazado' (chain stopped)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_happy_path_marks_solicitud_rechazado() -> None:
    """
    Reject active turn → both turno and solicitud move to 'rechazado'.
    Verifies execute is called twice (marcar_turno + actualizar_estado_solicitud).
    """
    conn = _make_conn()
    sol_pendiente = _sol_row(estado="pendiente")
    sol_rechazado = _sol_row(estado="rechazado")
    turno = _turno_row(orden=1, firmante_id=USER, estado="pendiente")
    turno_rechazado = _turno_row(orden=1, firmante_id=USER, estado="rechazado")

    # fetchval: check_permission(True) | es_firmante(1) | audit.append→prev_hash(None)
    conn.fetchval.side_effect = [True, 1, None]
    conn.fetchrow.side_effect = [
        sol_pendiente,   # get_or_404
        {},              # resolve_caller_clearance (rechazar._assert_can_read) → clearance=1 → OK
        turno,           # turno_activo (early, non-locking)
        turno,           # turno_activo_for_update (inside txn, row-lock)
        sol_rechazado,   # get() get_solicitud (after rejection)
        {},              # resolve_caller_clearance (get()._assert_can_read) → clearance=1 → OK
    ]
    conn.fetch.return_value = [turno_rechazado]  # list_turnos
    conn.execute.side_effect = _exec_sig  # 'UPDATE 1' para firma_cadena, None resto

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/reject",
                json={"motivo": "Documento incorrecto — requiere corrección"},
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["estado"] == "rechazado"
        # actualizar_estado_solicitud (UPDATE firma_solicitud) must be called with 'rechazado'
        firma_sol_updates = [
            c for c in conn.execute.call_args_list
            if "firma_solicitud" in str(c.args[0])
        ]
        assert len(firma_sol_updates) == 1
        assert firma_sol_updates[0].args[2] == "rechazado"
    finally:
        _clear()


# ---------------------------------------------------------------------------
# POST /reject — wrong turn → 403 no_es_su_turno
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_wrong_turn_returns_403() -> None:
    """Actor is in the chain but active turn belongs to another signer → 403."""
    conn = _make_conn()
    sol = _sol_row()
    turno_other = _turno_row(orden=1, firmante_id=FIRMANTE2)

    # fetchval: [check_permission(True), es_firmante(1=True)]
    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.side_effect = [
        sol,          # get_or_404
        {},           # resolve_caller_clearance (rechazar._assert_can_read) → clearance=1 → OK
        turno_other,  # turno_activo (firmante_id == FIRMANTE2, not USER)
    ]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/reject",
                json={"motivo": "Rechazar por error"},
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "no_es_su_turno"
    finally:
        _clear()


# ---------------------------------------------------------------------------
# POST /reject — not in chain → 403 no_es_firmante
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_not_firmante_returns_403() -> None:
    """Actor not in the chain at all → 403 no_es_firmante (before turn check)."""
    conn = _make_conn()
    sol = _sol_row()

    # fetchval: [check_permission(True), es_firmante(None=False)]
    conn.fetchval.side_effect = [True, None]
    conn.fetchrow.side_effect = [
        sol,  # get_or_404
        {},   # resolve_caller_clearance (_assert_can_read) → clearance=1, nivel=1 → OK
    ]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/reject",
                json={"motivo": "Rechazar"},
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "no_es_firmante"
    finally:
        _clear()


# ---------------------------------------------------------------------------
# POST /reject — empty motivo → 422 (Pydantic min_length=1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_empty_motivo_returns_422() -> None:
    """motivo is mandatory with min_length=1 — empty string fails Pydantic validation."""
    conn = _make_conn()
    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/reject",
                json={"motivo": ""},
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 422
    finally:
        _clear()


# ---------------------------------------------------------------------------
# POST /batch — mixed result: one firmado + one omitido (no abort on failure)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_mixed_firmado_and_omitido(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Batch signs first item (succeeds) and skips second (wrong turn).
    The batch must NOT abort: both results are returned, in order.

    2FA disabled (E06 Inc.2, RF-FIR-13): sid1 reaches the happy-path sign, which
    would otherwise require a mocked challenge lookup — see test_cadena_2fa.py
    for per-item OTP coverage of the batch endpoint.
    """
    monkeypatch.setattr(settings, "firma_2fa_required", False)
    conn = _make_conn()

    sid1 = uuid.uuid4()
    sid2 = uuid.uuid4()

    sol1 = _sol_row(estado="pendiente")
    sol1_firmado = _sol_row(estado="firmado")
    turno1 = _turno_row(orden=1, firmante_id=USER, estado="pendiente")
    turno1_firmado = _turno_row(
        orden=1, firmante_id=USER, estado="firmado", firma_id=FIRMA_ID
    )
    firma_ins = _firma_row()

    sol2 = _sol_row(estado="pendiente")
    turno2_other = _turno_row(orden=1, firmante_id=FIRMANTE2)  # not USER's turn

    # sid1 (firmar success, last turn) fetchrow order:
    #   get_or_404 | _assert_can_read | turno_activo(early) | turno_activo_for_update(txn) |
    #   firma_insert | next_active=None | get() get_solicitud | get()._assert_can_read
    # sid2 (no_es_su_turno) fetchrow order:
    #   get_or_404 | _assert_can_read | turno_activo(early) → FIRMANTE2 != USER → 403
    conn.fetchrow.side_effect = [
        sol1,          # sid1: get_or_404
        {},            # sid1: resolve_caller_clearance (_assert_can_read) → clearance=1 → OK
        turno1,        # sid1: turno_activo (early)
        turno1,        # sid1: turno_activo_for_update (inside txn)
        firma_ins,     # sid1: FirmaRepository.insert
        None,          # sid1: next_active → triggers actualizar_estado
        sol1_firmado,  # sid1: get() get_solicitud
        {},            # sid1: resolve_caller_clearance (get()._assert_can_read) → OK
        sol2,          # sid2: get_or_404
        {},            # sid2: resolve_caller_clearance (_assert_can_read) → clearance=1 → OK
        turno2_other,  # sid2: turno_activo (early) → FIRMANTE2 != USER → 403
    ]
    # fetchval: check_permission(True) | es_firmante_sid1(1) | audit_prev_hash(None) | es_firmante_sid2(1)
    conn.fetchval.side_effect = [True, 1, None, 1]
    conn.fetch.side_effect = [[turno1_firmado]]  # sid1: list_turnos (sid2 aborts before fetch)
    conn.execute.side_effect = _exec_sig  # 'UPDATE 1' para firma_cadena, None resto

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/signature/batch",
                json={
                    "items": [
                        {"solicitud_id": str(sid1), "challenge_id": str(uuid.uuid4()), "otp": "000000"},
                        {"solicitud_id": str(sid2), "challenge_id": str(uuid.uuid4()), "otp": "000000"},
                    ]
                },
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 200
        resultados = resp.json()["resultados"]
        assert len(resultados) == 2
        assert resultados[0]["resultado"] == "firmado"
        assert resultados[1]["resultado"] == "omitido"
        assert resultados[1]["detalle"] == "no_es_su_turno"
    finally:
        _clear()


# ---------------------------------------------------------------------------
# Authorization — 401 without X-User-Id, 403 without PERM_FIRMA (RF-FIR-12)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cadena_create_requires_auth() -> None:
    """POST /requests without X-User-Id header → 401 (require_permission gate, RF-FIR-12)."""
    conn = _make_conn()
    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/signature/requests",
                json={
                    "objeto_tipo": "radicado",
                    "objeto_id": "2024-DEMO-E-000001",
                    "firmantes": [str(USER)],
                },
                headers={"X-Tenant-Slug": "demo"},  # X-User-Id intentionally omitted
            )
        assert resp.status_code == 401
    finally:
        _clear()


@pytest.mark.asyncio
async def test_cadena_create_requires_perm_firma() -> None:
    """Valid X-User-Id but PERM_FIRMA denied in DB → 403 forbidden (RF-FIR-12)."""
    conn = _make_conn()
    conn.fetchval.return_value = None  # check_permission → denied (user not found / no perm)
    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/signature/requests",
                json={
                    "objeto_tipo": "radicado",
                    "objeto_id": "x",
                    "firmantes": [str(USER)],
                },
                headers=TENANT_HEADERS,  # includes X-User-Id
            )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "forbidden"
    finally:
        _clear()


# ===========================================================================
# RF-SEG-08 — no-read-up por clearance (D-15)
# ===========================================================================


@pytest.mark.asyncio
async def test_create_radicado_snaps_nivel_from_radicados_table() -> None:
    """Creating a chain for objeto_tipo='radicado' snapshots nivel from radicados table.

    resolver_nivel_radicado returns 3 → INSERT is called with nivel_seguridad=3.
    """
    conn = _make_conn()
    sol = _sol_row(nivel_seguridad=3)  # mock DB returns the stored row

    # fetchval order: [check_permission, resolver_nivel(3), existe_abierta(None), audit_prev(None)]
    conn.fetchval.side_effect = [True, 3, None, None]
    conn.fetchrow.side_effect = [
        {"is_root": False, "max_level": 3},  # resolve_caller_clearance → 3 (>= nivel 3, gate passes)
        sol,                                  # crear_solicitud RETURNING
    ]
    conn.fetch.return_value = []        # list_turnos

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/signature/requests",
                json={
                    "objeto_tipo": "radicado",
                    "objeto_id": "2024-DEMO-E-000001",
                    "firmantes": [str(USER)],
                },
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 201
        # Verify the INSERT was called with nivel_seguridad=3 (snapshotted from radicado).
        # call_args_list[0] is resolve_caller_clearance; [1] is crear_solicitud INSERT.
        insert_args = conn.fetchrow.call_args_list[1].args
        assert insert_args[-1] == 3, "nivel_seguridad snapshotted from radicados"
        assert resp.json()["nivel_seguridad"] == 3
    finally:
        _clear()


@pytest.mark.asyncio
async def test_get_request_nivel3_clearance1_returns_404() -> None:
    """GET /requests/{id}: actor with clearance 1 cannot see a nivel_seguridad=3 chain.

    The 404 must be indistinguishable from 'not found' (no-read-up, RF-SEG-08, D-15).
    """
    conn = _make_conn()
    sol = _sol_row(nivel_seguridad=3)
    # clearance row: is_root=False, max_level=1 → clearance=1; nivel=3 → 3 > 1 → 404
    clearance_row = {"is_root": False, "max_level": 1}
    conn.fetchrow.side_effect = [sol, clearance_row]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/signature/requests/{SOL_ID}",
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "solicitud_not_found"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_get_request_nivel3_clearance3_returns_200() -> None:
    """GET /requests/{id}: actor with clearance 3 can read a nivel_seguridad=3 chain."""
    conn = _make_conn()
    sol = _sol_row(nivel_seguridad=3)
    turno = _turno_row()
    # clearance row: max_level=3 → clearance=3; nivel=3 → 3 > 3 is False → passes
    clearance_row = {"is_root": False, "max_level": 3}
    conn.fetchrow.side_effect = [sol, clearance_row]
    conn.fetch.return_value = [turno]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/signature/requests/{SOL_ID}",
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 200
        assert resp.json()["nivel_seguridad"] == 3
    finally:
        _clear()


@pytest.mark.asyncio
async def test_pending_max_clearance_forwarded_to_repo() -> None:
    """GET /pending: resolve_caller_clearance result flows to pendientes_de_firmante as $4.

    Verifies that the bandeja query is passed max_clearance so nivel_seguridad > clearance
    items are silently excluded from both rows and total (RF-SEG-08, D-15).
    """
    conn = _make_conn()
    # resolve_caller_clearance returns clearance=2 (RESERVADA)
    clearance_row = {"is_root": False, "max_level": 2}
    conn.fetchrow.return_value = clearance_row
    conn.fetch.return_value = []  # no pending items at this clearance level

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(
                "/api/v1/signature/pending",
                params={"page": "1", "size": "20"},
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 200
        # $4 in the fetch call is max_clearance; args: (query, firmante_id, limit, offset, max_clearance)
        fetch_args = conn.fetch.call_args_list[0].args
        assert fetch_args[-1] == 2, "max_clearance=2 forwarded to pendientes_de_firmante"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_sign_nivel3_clearance1_returns_404() -> None:
    """firmar: actor with clearance 1 against nivel_seguridad=3 chain → 404, not 403/409.

    The gate fires before estado and firmante checks so the existence of the chain
    is never revealed (no-read-up, RF-SEG-08, D-15).
    """
    conn = _make_conn()
    sol = _sol_row(nivel_seguridad=3)
    clearance_row = {"is_root": False, "max_level": 1}
    # get_or_404 → sol; _assert_can_read → clearance_row (nivel 3 > clearance 1 → 404)
    conn.fetchrow.side_effect = [sol, clearance_row]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/sign",
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "solicitud_not_found"
        # es_firmante (fetchval #2) must NOT have been reached.
        assert conn.fetchval.call_count == 1
    finally:
        _clear()


@pytest.mark.asyncio
async def test_reject_nivel3_clearance1_returns_404() -> None:
    """rechazar: actor with clearance 1 against nivel_seguridad=3 chain → 404, not 403/409."""
    conn = _make_conn()
    sol = _sol_row(nivel_seguridad=3)
    clearance_row = {"is_root": False, "max_level": 1}
    conn.fetchrow.side_effect = [sol, clearance_row]

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/signature/requests/{SOL_ID}/reject",
                json={"motivo": "Intentando rechazar documento clasificado"},
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "solicitud_not_found"
        # es_firmante fetchval must NOT have been reached.
        assert conn.fetchval.call_count == 1
    finally:
        _clear()


# ===========================================================================
# `objeto_tipo` no puede apagar el control de nivel de seguridad (RF-SEG-08)
#
# El nivel de la cadena se resolvía comparando `payload.objeto_tipo` —texto libre
# del body— contra el literal "radicado". Quien creaba la cadena elegía así si le
# aplicaban la fuente autoritativa. Tres puertas, un mismo defecto:
#   1. variante de mayúsculas ("Radicado")
#   2. tipo distinto sobre el identificador de un radicado ("borrador")
#   3. identificador que no resuelve → se caía al nivel declarado en el body
# ===========================================================================


@pytest.mark.asyncio
async def test_objeto_tipo_fuera_del_allow_set_es_422() -> None:
    """"Radicado" ya no llega al servicio: el schema lo rechaza (422).

    Es la primera de las tres puertas y la más barata de cerrar — con el conjunto
    cerrado, la variante de mayúsculas ni siquiera se puede crear.
    """
    conn = _make_conn()
    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/signature/requests",
                json={
                    "objeto_tipo": "Radicado",
                    "objeto_id": "2024-DEMO-S-CLASIF",
                    "nivel_seguridad": 1,
                    "firmantes": [str(USER)],
                },
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 422
    finally:
        _clear()


@pytest.mark.asyncio
async def test_tipo_declarado_no_evita_la_resolucion_del_nivel_real() -> None:
    """Declarar "borrador" sobre el identificador de un radicado CLASIFICADA no
    rebaja nada: la resolución no consulta `objeto_tipo`, así que el nivel real (3)
    manda sobre el `nivel_seguridad: 1` del body y el gate no-read-up dispara."""
    conn = _make_conn()
    # fetchval: [check_permission, resolver_nivel_por_objeto(3 — pese al tipo declarado)]
    conn.fetchval.side_effect = [True, 3]
    conn.fetchrow.side_effect = [{}]  # resolve_caller_clearance → PUBLICA=1 < 3

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/signature/requests",
                json={
                    "objeto_tipo": "borrador",
                    "objeto_id": "2024-DEMO-S-CLASIF",
                    "nivel_seguridad": 1,  # declaración del cliente: se ignora
                    "firmantes": [str(USER)],
                    "payload_sha256": "b" * 64,
                },
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "solicitud_not_found"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_radicado_que_no_resuelve_es_404_no_nivel_declarado() -> None:
    """Un `objeto_tipo` que exige documento registrado y no resuelve se rechaza.

    Antes se caía al `nivel_seguridad` del body, con lo que bastaba un `objeto_id`
    que no resolviera para volver a declarar el nivel desde el cliente. El 404 es el
    mismo neutro del no-read-up: no distingue "no existe" de "no lo alcanzas".
    """
    conn = _make_conn()
    conn.fetchval.side_effect = [True, None]  # resolver_nivel_por_objeto → None

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/signature/requests",
                json={
                    "objeto_tipo": "radicado",
                    "objeto_id": "no-existe",
                    "nivel_seguridad": 1,
                    "firmantes": [str(USER)],
                },
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "solicitud_not_found"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_borrador_sin_objeto_registrado_conserva_el_nivel_declarado(
    monkeypatch,
) -> None:
    """Contrapeso del test anterior: un borrador NO es un documento registrado, no
    hay tabla que consultar, y el nivel que declara su autor sigue siendo válido.

    Sin este test, endurecer de más pasaría inadvertido — el otro defecto posible.

    La HUELLA sí tiene fuente autoritativa desde E23 (document-service), así que
    se sustituye la consulta: lo que este test comprueba es el nivel, y dejar que
    salga a la red lo convertiría en un test de integración encubierto.
    """
    from app.services import cadena_service as _mod

    async def _huella(borrador_id: str, tenant_slug: str) -> dict:
        return {"version": 1, "documento_sha256": "a" * 64}

    monkeypatch.setattr(_mod, "get_huella_borrador", _huella)
    conn = _make_conn()
    sol = _sol_row(nivel_seguridad=2)
    conn.fetchval.side_effect = [True, None, None, None]
    conn.fetchrow.side_effect = [
        {"is_root": False, "max_level": 2},  # clearance 2 ≥ nivel declarado 2
        sol,
    ]
    conn.fetch.return_value = []

    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/signature/requests",
                json={
                    "objeto_tipo": "borrador",
                    "objeto_id": "borrador-local-42",
                    "nivel_seguridad": 2,
                    "firmantes": [str(USER)],
                    # Obligatoria desde E23 (RF-PRO-09): un borrador se firma por
                    # su contenido, no por su nombre.
                    "payload_sha256": "a" * 64,
                },
                headers=TENANT_HEADERS,
            )
        assert resp.status_code == 201
        assert resp.json()["nivel_seguridad"] == 2
    finally:
        _clear()
