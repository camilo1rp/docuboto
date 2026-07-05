# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests for archive-service endpoints.

DB is fully mocked — no real infrastructure required.

Key mock call sequences:

CREATE EXPEDIENTE:
  execute  — SET search_path (next_expediente_code)
  execute  — upsert expediente_sequences
  fetchval — SELECT last_seq
  execute  — SET search_path (create_expediente)
  fetchrow — INSERT expedientes RETURNING

LIST EXPEDIENTES:
  execute  — SET search_path (fetchval)
  fetchval — COUNT
  execute  — SET search_path (fetch)
  fetch    — SELECT page

GET EXPEDIENTE BY ID:
  execute  — SET search_path
  fetchrow — SELECT expediente
  fetch    — SELECT radicados

LINK RADICADO (link_radicado calls get+exists+insert):
  execute  — SET search_path (get)
  fetchrow — SELECT expediente
  fetch    — SELECT radicados (empty)
  execute  — SET search_path (exists)
  fetchval — COUNT(*) for duplicate check (estado='vinculado')  → 0
  execute  — SET search_path (link)
  execute  — pg_advisory_xact_lock (orden_documental atómico, E15 perfil v3)
  fetchval — COALESCE(MAX(orden_documental),0)+1
  fetchrow — INSERT ... ON CONFLICT DO UPDATE expediente_radicados RETURNING

UNLINK RADICADO (E15 perfil v3, RT-15 art. 4.3.2.3 — SOFT-DELETE, ya NO DELETE físico):
  execute  — SET search_path
  execute  — UPDATE expediente_radicados SET estado='excluido' ...  (returns "UPDATE 1")

CREATE TRD SERIE:
  execute  — SET search_path
  fetchrow — INSERT trd_series RETURNING

LIST TRD SERIES:
  execute  — SET search_path (fetchval)
  fetchval — COUNT
  execute  — SET search_path (fetch)
  fetch    — SELECT page
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from tests.conftest import (
    EXPEDIENTE_ID,
    NOW,
    RADICADO_ID,
    SERIE_ID,
    TENANT_HEADERS,
    TRACKING_NUMBER,
    USER_ID,
    make_expediente_record,
    make_radicado_link_record,
    make_trd_serie_record,
)

_CREATE_EXP_PAYLOAD = {
    "name": "Contrato 2024-001",
    "description": "Contrato de prestación de servicios",
}

_CREATE_TRD_PAYLOAD = {
    "code": "100",
    "name": "Contratos",
    "retention_years": 5,
    "total_retention": 10,
    "disposition": "conserve",
}


# ---------------------------------------------------------------------------
# POST /api/v1/expedientes — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_expediente(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID, code="EXP-2024-0001")

    # fetchval returns the sequence number (1), fetchrow returns the new expediente.
    mock_conn.fetchval.return_value = 1
    mock_conn.fetchrow.return_value = exp_record

    response = await client.post(
        "/api/v1/expedientes",
        json=_CREATE_EXP_PAYLOAD,
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["code"] == "EXP-2024-0001"
    assert body["name"] == "Contrato 2024-001"
    assert body["status"] == "open"
    assert body["radicados"] == []


# ---------------------------------------------------------------------------
# POST /api/v1/expedientes — missing X-Tenant-Slug
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_expediente_missing_tenant(client: AsyncClient) -> None:
    """Sin X-Tenant-Slug, el gate RBAC (`require_permission`, dependency del
    decorador) corta la petición con 403 antes de que `_require_tenant` (Depends
    del handler) pueda emitir su propio 400 — `require_permission` exige el
    tenant para poder resolver el permiso y responde fail-closed (D-08)."""
    response = await client.post(
        "/api/v1/expedientes",
        json=_CREATE_EXP_PAYLOAD,
        headers={"X-User-Id": str(USER_ID)},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"


# ---------------------------------------------------------------------------
# GET /api/v1/expedientes — list with X-Total-Count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_expedientes(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    records = [
        make_expediente_record(exp_id=uuid.uuid4(), code="EXP-2024-0001"),
        make_expediente_record(exp_id=uuid.uuid4(), code="EXP-2024-0002"),
    ]
    mock_conn.fetchval.return_value = 2
    mock_conn.fetch.return_value = records

    response = await client.get(
        "/api/v1/expedientes",
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200
    assert response.headers["x-total-count"] == "2"
    body = response.json()
    assert len(body) == 2
    assert body[0]["code"] == "EXP-2024-0001"
    assert body[1]["code"] == "EXP-2024-0002"


# ---------------------------------------------------------------------------
# GET /api/v1/expedientes/{expediente_id} — includes radicados
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_expediente_by_id(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID)
    rad_record = make_radicado_link_record(expediente_id=EXPEDIENTE_ID, radicado_id=RADICADO_ID)
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    mock_conn.fetchrow.return_value = exp_record
    mock_conn.fetch.return_value = [rad_record]

    response = await client.get(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}",
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(EXPEDIENTE_ID)
    assert len(body["radicados"]) == 1
    assert body["radicados"][0]["radicado_id"] == str(RADICADO_ID)
    assert body["radicados"][0]["tracking_number"] == TRACKING_NUMBER


@pytest.mark.asyncio
async def test_get_expediente_by_id_is_clearance_scoped(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """RF-SEG-08: el GET de detalle de expediente acota por clasificación — la
    consulta filtra `nivel_seguridad <= clearance`."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    mock_conn.fetchrow.return_value = make_expediente_record(exp_id=EXPEDIENTE_ID)
    mock_conn.fetch.return_value = []

    resp = await client.get(f"/api/v1/expedientes/{EXPEDIENTE_ID}", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    # La consulta de detalle incluye el predicado de clearance.
    assert "nivel_seguridad <=" in mock_conn.fetchrow.call_args.args[0]


@pytest.mark.asyncio
async def test_list_expediente_eventos_is_clearance_scoped(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """E02 §9 / RF-SEG-08: la hoja de ruta del expediente se acota por la visibilidad del
    objeto para el clearance del llamante (Canal B), NO por permiso de admin."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    mock_conn.fetchrow.return_value = None  # usuario sin clearance → PUBLICA (1)
    mock_conn.fetch.return_value = []

    resp = await client.get(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/eventos", headers=TENANT_HEADERS
    )
    assert resp.status_code == 200
    eventos_sql = mock_conn.fetch.call_args[0][0]
    assert "expediente_eventos" in eventos_sql
    assert "nivel_seguridad" in eventos_sql and "<=" in eventos_sql
    assert mock_conn.fetch.call_args[0][2] == 1  # clearance PUBLICA como parámetro


# ---------------------------------------------------------------------------
# GET /api/v1/expedientes/{expediente_id} — not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_expediente_not_found(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    mock_conn.fetchrow.return_value = None

    response = await client.get(
        f"/api/v1/expedientes/{uuid.uuid4()}",
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "expediente_not_found"


# ---------------------------------------------------------------------------
# PATCH /api/v1/expedientes/{expediente_id} — close (open→closed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_expediente_status(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    open_record = make_expediente_record(exp_id=EXPEDIENTE_ID, exp_status="open")
    closed_record = make_expediente_record(exp_id=EXPEDIENTE_ID, exp_status="closed")

    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    # First fetchrow → get_expediente_by_id (current state check)
    # Second fetchrow → update_expediente RETURNING
    mock_conn.fetchrow.side_effect = [open_record, closed_record]
    # fetch → radicados list (empty for get_expediente_by_id call)
    mock_conn.fetch.return_value = []

    response = await client.patch(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}",
        json={"status": "closed"},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "closed"


@pytest.mark.asyncio
async def test_update_expediente_disposition_requires_crud_3(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """El PATCH que transiciona a closed/transferred es DISPOSICIÓN y exige
    crud>=3 (Crear/Borrar), igual que /close y /transfer — cierra la ruta alterna
    que evadía el umbral reforzado (Ley 594/2000 arts. 22-24). Con el permiso solo
    a nivel Leer (gate del decorador pasa con crud>=1, el check condicional de
    disposición no), responde 403 sin ejecutar la transición."""
    client, mock_conn = client_with_conn
    # 1.º fetchval = gate del decorador (min_crud=1) → True (tiene el permiso).
    # 2.º fetchval = check condicional de disposición (min_crud=3) → False.
    mock_conn.fetchval.side_effect = [True, False]

    response = await client.patch(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}",
        json={"status": "transferred"},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"
    # El 403 corta ANTES del servicio: la transición no se ejecutó.
    mock_conn.fetchrow.assert_not_called()


# ---------------------------------------------------------------------------
# POST /api/v1/expedientes/{expediente_id}/radicados — link radicado
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_link_radicado(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID)
    link_record = make_radicado_link_record(expediente_id=EXPEDIENTE_ID, radicado_id=RADICADO_ID)

    # fetchrow: get_expediente, link INSERT..ON CONFLICT RETURNING, [regenerate:
    # get_expediente, get_current(None), insert_version índice]
    mock_conn.fetchrow.side_effect = [exp_record, link_record, exp_record, None, exp_record]
    # fetch → radicados (empty during get_expediente check)
    mock_conn.fetch.return_value = []
    # fetchval: [0]=D-08 gate (USUA_PERM_EXPEDIENTE granted), [1]=COUNT(*) duplicate
    # check (estado='vinculado') → 0, [2]=MAX(orden_documental)+1 (E15 perfil v3,
    # asignación atómica del orden ESTABLE), [3]=audit.append prev_hash lookup
    # (regenerate_index → índice E15, ADR-010)
    mock_conn.fetchval.side_effect = [True, 0, 1, None]

    response = await client.post(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados",
        json={"radicado_id": str(RADICADO_ID), "tracking_number": TRACKING_NUMBER},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["radicado_id"] == str(RADICADO_ID)
    assert body["tracking_number"] == TRACKING_NUMBER


# ---------------------------------------------------------------------------
# POST /api/v1/expedientes/{expediente_id}/radicados — duplicate → 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_link_radicado_duplicate(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID)

    mock_conn.fetchrow.return_value = exp_record
    mock_conn.fetch.return_value = []
    # fetchval → 1 means the link already exists
    mock_conn.fetchval.return_value = 1

    response = await client.post(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados",
        json={"radicado_id": str(RADICADO_ID), "tracking_number": TRACKING_NUMBER},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "radicado_already_linked"


# ---------------------------------------------------------------------------
# DELETE /api/v1/expedientes/{expediente_id}/radicados/{radicado_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unlink_radicado(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    # E15 perfil v3 (RT-15 art. 4.3.2.3): exclusión = SOFT-DELETE — el UPDATE
    # devuelve "UPDATE 1" (ya no un DELETE físico).
    mock_conn.execute.return_value = "UPDATE 1"
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID)
    # fetchrow: get_expediente (guard), [regenerate: get_expediente,
    # get_current(None), insert_version índice]
    mock_conn.fetchrow.side_effect = [exp_record, exp_record, None, exp_record]
    mock_conn.fetch.return_value = []

    response = await client.delete(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados/{RADICADO_ID}",
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 204


@pytest.mark.asyncio
async def test_unlink_radicado_audits_actor_and_causal(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """(Media, cierre RT-15) La exclusión (soft-delete) deja su propio asiento
    en `audit_log` (action='archive.radicado_excluido') con el actor
    (X-User-Id de quien excluye) y el causal — antes solo quedaba en
    `expediente_eventos` con actor=None, sin identificar al agente
    responsable (Ley 594/2000; Ac. AGN 001/2024 art. 4.3.2.3)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    mock_conn.execute.return_value = "UPDATE 1"
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID)
    mock_conn.fetchrow.side_effect = [exp_record, exp_record, None, exp_record]
    mock_conn.fetch.return_value = []

    response = await client.delete(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados/{RADICADO_ID}",
        params={"causal": "error de captura"},
        headers=TENANT_HEADERS,
    )
    assert response.status_code == 204

    # 1) el evento append-only de la hoja de ruta lleva el actor.
    evento_call = next(
        c
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO expediente_eventos" in c.args[0]
    )
    assert evento_call.args[4] == USER_ID  # (expediente_id, numero, accion, actor, ...)

    # 2) el audit_log inmutable (Canal A) también lleva actor + causal.
    audit_calls = [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    # args: (sql, tenant, service, actor, canal, action, object_type, object_ref, payload, prev_hash, hash)
    radicado_excluido = next(a for a in audit_calls if a[5] == "archive.radicado_excluido")
    assert radicado_excluido[3] == str(USER_ID)
    assert "error de captura" in radicado_excluido[8]


# ---------------------------------------------------------------------------
# POST /api/v1/trd — create TRD serie
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_trd_serie(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # USUA_PERM_TRD granted (gate)
    serie_record = make_trd_serie_record(serie_id=SERIE_ID)
    mock_conn.fetchrow.return_value = serie_record

    response = await client.post(
        "/api/v1/trd",
        json=_CREATE_TRD_PAYLOAD,
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == str(SERIE_ID)
    assert body["code"] == "100"
    assert body["name"] == "Contratos"
    assert body["disposition"] == "conserve"
    assert body["is_active"] is True


# ---------------------------------------------------------------------------
# GET /api/v1/trd — list series with X-Total-Count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_trd_series(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    records = [
        make_trd_serie_record(serie_id=uuid.uuid4(), code="100"),
        make_trd_serie_record(serie_id=uuid.uuid4(), code="200"),
    ]
    mock_conn.fetchval.return_value = 2
    mock_conn.fetch.return_value = records

    response = await client.get(
        "/api/v1/trd",
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200
    assert response.headers["x-total-count"] == "2"
    body = response.json()
    assert len(body) == 2
    assert body[0]["code"] == "100"
    assert body[1]["code"] == "200"


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1

    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


@pytest.mark.asyncio
async def test_link_radicados_batch(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Inclusión masiva: vincula los nuevos, omite los ya vinculados, regenera índice 1 vez."""
    client, mock_conn = client_with_conn
    exp = make_expediente_record(exp_id=EXPEDIENTE_ID, exp_status="open")
    r1, r2 = uuid.uuid4(), uuid.uuid4()
    # get_expediente_by_id (fetchrow exp) ; luego por cada radicado: link INSERT (fetchrow);
    # al final regenerate: get_expediente_by_id (fetchrow exp), get_current (None), insert índice
    mock_conn.fetchrow.side_effect = [
        exp,  # _expediente_or_404
        make_radicado_link_record(radicado_id=r1),  # link r1 INSERT..ON CONFLICT RETURNING
        make_radicado_link_record(radicado_id=r2),  # link r2 INSERT..ON CONFLICT RETURNING
        exp,
        None,
        exp,  # regenerate (get_exp, get_current, insert)
    ]
    mock_conn.fetch.return_value = []
    # fetchval: [0]=D-08 gate, [1]=radicado_link_exists r1 → 0, [2]=MAX(orden_documental)+1
    # para r1 (E15 perfil v3), [3]=radicado_link_exists r2 → 0, [4]=MAX(orden_documental)+1
    # para r2, [5]=audit.append prev_hash lookup (regenerate_index, 1 sola vez tras el batch)
    mock_conn.fetchval.side_effect = [True, 0, 1, 0, 2, None]

    resp = await client.post(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados/batch",
        json={
            "radicados": [
                {"radicado_id": str(r1), "tracking_number": "2024-A-001"},
                {"radicado_id": str(r2), "tracking_number": "2024-A-002"},
            ]
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["vinculados"] == 2 and body["omitidos"] == 0 and body["total"] == 2


# ---------------------------------------------------------------------------
# RBAC gate (D-08): USUA_PERM_EXPEDIENTE en los 12 endpoints de expedientes.py
# (Alineación completa con indice.py/fisico.py/transferencias.py — cierre del
# gap: antes, cualquier usuario autenticado del tenant podía operar sobre
# expedientes sin el permiso, mutaciones y lecturas por igual.)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_expediente_forbidden_without_permission(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Mutación representativa: sin USUA_PERM_EXPEDIENTE, `require_permission`
    corta con 403 antes de tocar la lógica de negocio (no se genera secuencia)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False  # D-08: permiso denegado

    response = await client.post(
        "/api/v1/expedientes",
        json=_CREATE_EXP_PAYLOAD,
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_get_expediente_forbidden_without_permission(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Lectura representativa: sin USUA_PERM_EXPEDIENTE, GET también es 403 —
    D-08 es alineación completa, las lecturas no quedan exentas del gate."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False  # D-08: permiso denegado

    response = await client.get(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}",
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_create_expediente_forbidden_without_user_id(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """`require_permission` es fail-closed sobre la IDENTIDAD: sin X-User-Id no
    hay usuario que resolver → 403, sin necesidad de que la BD deniegue nada
    (check_permission ni siquiera consulta con user_id=None)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # aunque "hubiera" permiso, sin identidad no aplica

    response = await client.post(
        "/api/v1/expedientes",
        json=_CREATE_EXP_PAYLOAD,
        headers={"X-Tenant-Slug": TENANT_HEADERS["X-Tenant-Slug"]},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_get_expediente_forbidden_without_user_id(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Misma conducta fail-closed para una lectura: sin X-User-Id, 403 antes de
    consultar el expediente."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    response = await client.get(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}",
        headers={"X-Tenant-Slug": TENANT_HEADERS["X-Tenant-Slug"]},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()


# ---------------------------------------------------------------------------
# E15 perfil v4 (RT-15, Acuerdo AGN 001/2024 art. 4.3.2.3) — residuales R3/R4
# cerrados: pista de acceso auditada + snapshot vivo de roles autorizados.
# ---------------------------------------------------------------------------


def _audit_inserts(mock_conn, action: str) -> list:
    return [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str)
        and "INSERT INTO public.audit_log" in c.args[0]
        and c.args[5] == action
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("canal", ["api", "ui", "mcp"])
async def test_get_expediente_audits_read_access_for_user_channels(
    client_with_conn: tuple[AsyncClient, AsyncMock], canal: str
) -> None:
    """(v4, R3-a) Una lectura EXITOSA de `GET /expedientes/{id}` deja pista de
    acceso en `audit_log` — atestada (sin contenido) en `<pistaAuditoria>`
    del índice y consultable vía `GET /historial-acceso`."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08 + advisory lock/prev_hash de audit.append
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID)
    mock_conn.fetchrow.return_value = exp_record
    mock_conn.fetch.return_value = []

    resp = await client.get(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}",
        headers={**TENANT_HEADERS, "X-Canal": canal},
    )
    assert resp.status_code == 200
    calls = _audit_inserts(mock_conn, "archive.expediente_consultado")
    assert len(calls) == 1
    assert calls[0][6] == "expediente"  # object_type
    assert calls[0][7] == exp_record["code"]  # object_ref


@pytest.mark.asyncio
async def test_get_expediente_does_not_audit_system_channel(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Sin `X-Canal` (llamada interna/sin pasar por el gateway) => 'system' —
    no deja pista de acceso (evita ensuciar el historial con llamadas que no
    son consultas de un usuario real)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = make_expediente_record(exp_id=EXPEDIENTE_ID)
    mock_conn.fetch.return_value = []

    resp = await client.get(f"/api/v1/expedientes/{EXPEDIENTE_ID}", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert _audit_inserts(mock_conn, "archive.expediente_consultado") == []


@pytest.mark.asyncio
async def test_audit_read_access_does_not_write_without_user_id() -> None:
    """(hallazgo Baja, cierre v4) `_audit_read_access` con canal de usuario
    real ('api') pero `user_id=None` NO escribe `audit_log` — el contrato
    R3-a exige un ACTOR real en la pista de acceso; sin él, `actor=None`
    dejaría una traza sin valor forense. Se prueba el método directamente
    (no vía HTTP): `require_permission` en el router ya exige `X-User-Id`
    resoluble para conceder `USUA_PERM_EXPEDIENTE` (fail-closed en
    `check_permission`), así que este estado — canal auditable + actor
    ausente — solo es alcanzable si `ArchiveService` se invoca directamente
    (otro router, MCP, un futuro llamador interno); la guarda debe sostenerse
    igual en ese caso."""
    from app.services.archive_service import ArchiveService

    mock_conn = AsyncMock()
    svc = ArchiveService(mock_conn, "demo")

    await svc._audit_read_access("EXP-2024-0001", user_id=None, canal="api")

    mock_conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_roles_autorizados_requires_permission(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False  # permiso denegado
    resp = await client.get(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/roles-autorizados", headers=TENANT_HEADERS
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_roles_autorizados_clearance_scoped_returns_404(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """No-read-up (RF-SEG-08): mismo criterio 404 que el resto de lecturas del
    expediente — ANTES de tocar la matriz RBAC."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # permiso concedido
    # resolve_caller_clearance (None -> PUBLICA) + get_expediente_by_id (None:
    # nivel_seguridad del expediente supera la clearance del llamante).
    mock_conn.fetchrow.side_effect = [None, None]
    mock_conn.fetch.return_value = []

    resp = await client.get(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/roles-autorizados", headers=TENANT_HEADERS
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "expediente_not_found"


@pytest.mark.asyncio
async def test_roles_autorizados_returns_live_matrix(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """(v4, R4-b) Snapshot VIVO — no firmado — de qué grupos pueden LEER el
    expediente ahora mismo: `crud>=1` sobre USUA_PERM_EXPEDIENTE y
    `role_clearance.max_level` que alcance el `nivel_seguridad`."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID, nivel_seguridad=2)
    mock_conn.fetchrow.side_effect = [None, exp_record]
    mock_conn.fetch.side_effect = [
        [],  # radicados
        [
            {"grupo": "archivistas", "clearance_max": 3, "crud": 4},
            {"grupo": "gestores", "clearance_max": 2, "crud": 1},
        ],
    ]

    resp = await client.get(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/roles-autorizados", headers=TENANT_HEADERS
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["grupo"] == "archivistas"
    assert body[0]["crud"] == 4
    # `nivel_seguridad` del expediente (no clearance del llamante) decide el filtro.
    sql, nivel = mock_conn.fetch.call_args_list[-1].args
    assert nivel == 2


@pytest.mark.asyncio
async def test_historial_acceso_requires_permission(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False
    resp = await client.get(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/historial-acceso", headers=TENANT_HEADERS
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_historial_acceso_clearance_scoped_returns_404(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [None, None]
    mock_conn.fetch.return_value = []

    resp = await client.get(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/historial-acceso", headers=TENANT_HEADERS
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "expediente_not_found"


@pytest.mark.asyncio
async def test_historial_acceso_lists_paginated_and_scoped_by_tenant(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """(v4, R3-b) Consulta de `public.audit_log` filtrada por expediente,
    paginada — el consumidor de esta vista NO ve `id`/`prev_hash`/`hash`
    internos de la cadena, solo el hecho auditado."""
    client, mock_conn = client_with_conn
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID)
    # fetchval: check_permission, luego COUNT(*) de AuditReadRepository.
    mock_conn.fetchval.side_effect = [True, 3]
    mock_conn.fetchrow.side_effect = [None, exp_record]
    entry = {
        "tenant_slug": TENANT_HEADERS["X-Tenant-Slug"],
        "ts": NOW,
        "service": "archive-service",
        "actor": str(USER_ID),
        "canal": "api",
        "action": "archive.expediente_consultado",
        "object_type": "expediente",
        "object_ref": exp_record["code"],
        "payload": {},
    }
    mock_conn.fetch.side_effect = [[], [entry]]

    resp = await client.get(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/historial-acceso?page=1&size=20",
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.headers["X-Total-Count"] == "3"
    body = resp.json()
    assert len(body) == 1
    assert body[0]["action"] == "archive.expediente_consultado"
    assert body[0]["object_ref"] == exp_record["code"]
    assert "id" not in body[0]
    assert "hash" not in body[0]
    # Tenant scoping: la SQL de conteo/list filtra por tenant_slug + object_ref.
    count_sql, tenant_arg, code_arg, _types = mock_conn.fetchval.call_args_list[-1].args
    assert tenant_arg == TENANT_HEADERS["X-Tenant-Slug"]
    assert code_arg == exp_record["code"]
