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

LIST EXPEDIENTES (RF-SEG-08 no-read-up: caller_clearance ANTES del listado):
  execute  — SET search_path (caller_clearance)
  fetchrow — resolve_caller_clearance (auth_users + role_clearance)
  execute  — SET search_path (fetchval)
  fetchval — COUNT (WHERE incluye nivel_seguridad <= clearance)
  execute  — SET search_path (fetch)
  fetch    — SELECT page (mismo predicado/parámetro de clearance que el COUNT)

GET EXPEDIENTE BY ID (RF-SEG-08 no-read-up: caller_clearance ANTES del get):
  execute  — SET search_path (caller_clearance)
  fetchrow — resolve_caller_clearance (auth_users + role_clearance)
  execute  — SET search_path
  fetchrow — SELECT expediente (WHERE nivel_seguridad <= clearance)
  fetch    — SELECT radicados

PATCH EXPEDIENTE (RF-SEG-08 no-read-up; 404 no 403 si excede el clearance;
`status` fue retirado del schema — cualquier valor 422 `extra_forbidden` ANTES
de tocar la BD, ver test_update_expediente_rechaza_status):
  execute  — SET search_path (caller_clearance)
  fetchrow — resolve_caller_clearance
  execute  — SET search_path (get_expediente_by_id)
  fetchrow — SELECT expediente (WHERE nivel_seguridad <= clearance) — None → 404
  fetch    — SELECT radicados (solo si el expediente SÍ es visible)
  execute  — SET search_path (repo.update_expediente)
  fetchrow — WITH old AS (... FOR UPDATE) UPDATE expedientes ... RETURNING e.*, old_*
  -- si hubo cambios efectivos (metadata_audit):
  execute  — INSERT expediente_eventos (accion='actualizado'|'reclasificado')
  execute  — pg_advisory_xact_lock (audit.append, cadena del tenant)
  fetchval — SELECT hash (prev_hash de audit_log)
  execute  — INSERT audit_log (action='archive.expediente_updated'|'archive.expediente_reclasificado')

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

import json
import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

import app.services.archive_service as archive_service_module
from app.schemas.archive import ExpedienteCreate, RadicadoLink
from tests.conftest import (
    EXPEDIENTE_ID,
    NOW,
    RADICADO_ID,
    SERIE_ID,
    TENANT_HEADERS,
    TENANT_SLUG,
    TRACKING_NUMBER,
    USER_ID,
    make_expediente_record,
    make_expediente_update_row,
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
# POST /api/v1/expedientes — M3 no-write-up de nivel_seguridad al ABRIR
# (RF-SEG-08): `ArchiveService.create_expediente` resuelve el clearance del
# creador ANTES del INSERT y rechaza con 403 si `nivel_seguridad` pedido
# excede lo que ese llamante puede ver. Espejo de la reclasificación de
# document-service: 403 explícito, nunca un clamp silencioso.
# ---------------------------------------------------------------------------


def _expedientes_insert_calls(mock_conn: AsyncMock) -> list:
    """Llamadas `fetchrow` que ejecutan el INSERT de `expedientes` (no el del
    índice `expediente_indice`, que también pasa por `fetchrow`)."""
    return [
        c.args
        for c in mock_conn.fetchrow.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO expedientes" in c.args[0]
    ]


@pytest.mark.asyncio
async def test_create_expediente_above_own_clearance_is_403(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Regresión del gate M3: un creador con clearance PUBLICA (1) que pide
    `nivel_seguridad=3` (CLASIFICADA) recibe 403 ANTES de cualquier INSERT.
    Si alguien quita el gate en `ArchiveService.create_expediente`, este test
    debe fallar (dejaría de ser 403, o el INSERT se ejecutaría)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    # Única fetchrow esperada: resolve_caller_clearance → PUBLICA (1).
    mock_conn.fetchrow.side_effect = [{"is_root": False, "max_level": 1}]

    response = await client.post(
        "/api/v1/expedientes",
        json={**_CREATE_EXP_PAYLOAD, "nivel_seguridad": 3},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"
    # El gate corta ANTES del INSERT: una sola fetchrow (la del clearance).
    assert mock_conn.fetchrow.call_count == 1
    assert _expedientes_insert_calls(mock_conn) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("nivel_seguridad", "expected_status"),
    [
        (3, 403),  # CLASIFICADA > RESERVADA(2) del creador → rechazado
        (2, 201),  # igual al clearance → permitido
        (1, 201),  # por debajo del clearance → permitido
    ],
)
async def test_create_expediente_no_write_up_by_level(
    client_with_conn: tuple[AsyncClient, AsyncMock],
    nivel_seguridad: int,
    expected_status: int,
) -> None:
    """No-write-up estricto: con clearance RESERVADA (2), igual-o-por-debajo
    pasa (2, 1); por encima (3) no. No es un límite fijo en PUBLICA — se
    contrasta contra el clearance REAL del creador."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1  # D-08 gate + next_expediente_code/audit
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID, nivel_seguridad=nivel_seguridad)
    if expected_status == 403:
        mock_conn.fetchrow.side_effect = [{"is_root": False, "max_level": 2}]
    else:
        mock_conn.fetchrow.side_effect = [
            {"is_root": False, "max_level": 2},  # resolve_caller_clearance → RESERVADA(2)
            exp_record,  # INSERT expedientes RETURNING
            exp_record,  # INSERT expediente_indice RETURNING (E15 v0)
        ]

    response = await client.post(
        "/api/v1/expedientes",
        json={**_CREATE_EXP_PAYLOAD, "nivel_seguridad": nivel_seguridad},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == expected_status
    if expected_status == 403:
        assert response.json()["detail"]["error"] == "forbidden"
        assert _expedientes_insert_calls(mock_conn) == []
    else:
        insert_calls = _expedientes_insert_calls(mock_conn)
        assert len(insert_calls) == 1
        assert insert_calls[0][8] == nivel_seguridad  # $8 = nivel_seguridad


@pytest.mark.asyncio
async def test_create_expediente_publica_default_with_publica_clearance(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Camino feliz que usa el frontend: clearance PUBLICA (1) creando con el
    `nivel_seguridad` por defecto (1, no enviado en el payload) — el gate M3
    no debe interferir con el caso común."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID, nivel_seguridad=1)
    mock_conn.fetchrow.side_effect = [
        {"is_root": False, "max_level": 1},  # resolve_caller_clearance → PUBLICA(1)
        exp_record,
        exp_record,
    ]

    response = await client.post(
        "/api/v1/expedientes",
        json=_CREATE_EXP_PAYLOAD,  # sin nivel_seguridad -> default 1
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    insert_calls = _expedientes_insert_calls(mock_conn)
    assert len(insert_calls) == 1
    assert insert_calls[0][8] == 1


@pytest.mark.asyncio
async def test_create_expediente_rejection_is_not_a_silent_clamp(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """El 403 es TOTAL: no se crea un expediente con el nivel rebajado a la
    baja en su lugar — eso sería una desclasificación de facto silenciosa.
    Contraparte explícita de `test_create_expediente_above_own_clearance_is_403`,
    verificando la ausencia del INSERT en vez de solo el status code."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [{"is_root": False, "max_level": 1}]  # PUBLICA(1)

    response = await client.post(
        "/api/v1/expedientes",
        json={**_CREATE_EXP_PAYLOAD, "nivel_seguridad": 2},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 403
    body = response.json()
    assert "code" not in body  # no se devolvió un expediente creado (ni rebajado)
    assert _expedientes_insert_calls(mock_conn) == []


@pytest.mark.asyncio
async def test_create_expediente_audit_includes_nivel_seguridad(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Cuando la creación es válida con `nivel_seguridad>=2`, el asiento
    `archive.expediente_created` en `audit_log` incluye `nivel_seguridad` en
    su payload — deja motivado el acto de clasificación desde la apertura."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID, nivel_seguridad=2)
    mock_conn.fetchrow.side_effect = [
        {"is_root": True, "max_level": None},  # CLASIFICADA(3), sobra para pedir 2
        exp_record,
        exp_record,
    ]

    response = await client.post(
        "/api/v1/expedientes",
        json={**_CREATE_EXP_PAYLOAD, "nivel_seguridad": 2},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    calls = _audit_inserts(mock_conn, "archive.expediente_created")
    assert len(calls) == 1
    payload = json.loads(calls[0][8])
    assert payload["nivel_seguridad"] == 2


# ---------------------------------------------------------------------------
# Atomicidad expediente<->auditoría (residual cerrado): las escrituras de
# `ArchiveService.create_expediente` (código+INSERT+índice v0+evento de
# apertura+asiento forense) están envueltas en `async with
# self._conn.transaction():`; el gate 403/validación de plantilla quedan
# FUERA. Con `conn` mockeado no se puede aseverar el ROLLBACK real (eso es
# comportamiento de Postgres en runtime) — lo que sí se cubre aquí es la
# ESTRUCTURA y el comportamiento OBSERVABLE con mocks: el wrap existe, un
# fallo tardío se propaga (no hay "éxito a medias"), y el gate no abre tx.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_expediente_wraps_writes_in_a_transaction(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Anti-regresión del wrapping: si alguien quita el `async with
    self._conn.transaction():` de `create_expediente`, este test debe fallar.
    `IndexService.insert_version` (índice v0) y `audit.append` abren cada uno
    su propia (sub)transacción — anidada, degrada a SAVEPOINT — así que un
    create exitoso sin `trd_serie_id` (sin validación de plantilla) invoca
    `conn.transaction()` exactamente 3 veces: el wrap de `create_expediente`
    + el de `insert_version` + el de `audit.append`. Si el wrap de
    `create_expediente` desapareciera, el conteo caería a 2."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1  # D-08 gate + next_expediente_code/audit prev_hash
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID)
    mock_conn.fetchrow.side_effect = [
        {"is_root": True, "max_level": None},  # resolve_caller_clearance
        exp_record,  # INSERT expedientes RETURNING
        exp_record,  # INSERT expediente_indice RETURNING (v0)
    ]

    response = await client.post(
        "/api/v1/expedientes", json=_CREATE_EXP_PAYLOAD, headers=TENANT_HEADERS
    )

    assert response.status_code == 201
    assert mock_conn.transaction.called
    assert mock_conn.transaction.call_count == 3


@pytest.mark.asyncio
async def test_create_expediente_late_failure_propagates_not_a_fake_success(
    client_with_conn: tuple[AsyncClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un fallo TARDÍO dentro de la transacción (aquí: `audit.append`, el
    último paso de la secuencia envuelta) debe PROPAGARSE — `create_expediente`
    nunca debe devolver un `ExpedienteResponse` de éxito si el asiento
    forense no se pudo escribir. Sustituye `audit.append` por completo (en
    vez de forzar un fallo interno de asyncpg) — mismo patrón ya documentado
    para simular fallos de auditoría en `orpycamcp_common.audit.append_denial`
    (nota de llamador). Invoca `ArchiveService.create_expediente` DIRECTAMENTE
    (no vía HTTP): Starlette's `ServerErrorMiddleware` reraise la excepción
    hacia el transporte ASGI DESPUÉS de invocar el exception handler global de
    `app/main.py` (para que el servidor/test runner la vea), así que pasar por
    `httpx.AsyncClient` aquí solo añadiría una capa intermedia — la aserción
    relevante es que la excepción sale de la corrutina del SERVICIO, no que se
    convierta en un 500 HTTP."""
    _, mock_conn = client_with_conn
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID)
    mock_conn.fetchrow.side_effect = [
        {"is_root": True, "max_level": None},  # resolve_caller_clearance
        exp_record,  # INSERT expedientes RETURNING
        exp_record,  # INSERT expediente_indice RETURNING (v0)
    ]

    async def _boom(*args: object, **kwargs: object) -> str:
        raise RuntimeError("audit failure (simulated)")

    monkeypatch.setattr(archive_service_module.audit, "append", _boom)

    service = archive_service_module.ArchiveService(mock_conn, TENANT_SLUG)
    payload = ExpedienteCreate(**_CREATE_EXP_PAYLOAD)

    with pytest.raises(RuntimeError, match="audit failure"):
        await service.create_expediente(payload, USER_ID)

    # Llegó a intentar el INSERT de `expedientes` (y el evento de apertura)
    # antes de que `audit.append` reventara — el fallo es TARDÍO, no previo.
    assert _expedientes_insert_calls(mock_conn) != []
    evento_calls = [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO expediente_eventos" in c.args[0]
    ]
    assert evento_calls != []


@pytest.mark.asyncio
async def test_create_expediente_403_gate_never_opens_a_transaction(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """El gate no-write-up (M3) queda DELIBERADAMENTE fuera de `async with
    self._conn.transaction():` — no hay nada que revertir y no conviene
    mantener la tx abierta durante la validación. Cuando el gate rechaza
    (403), NUNCA debe abrirse una transacción: `mock_conn.transaction` debe
    quedar en `call_count == 0` (D-08 ya pasó, `check_permission` solo usa
    `fetchval`, sin `transaction()`)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    mock_conn.fetchrow.side_effect = [{"is_root": False, "max_level": 1}]  # PUBLICA(1)

    response = await client.post(
        "/api/v1/expedientes",
        json={**_CREATE_EXP_PAYLOAD, "nivel_seguridad": 3},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 403
    assert mock_conn.transaction.call_count == 0


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
    # fetchrow determinista (RF-SEG-08): sin membresías → PUBLICA (1). Dejarlo
    # sin configurar dejaría pasar un AsyncMock truthy no configurado por
    # `resolve_caller_clearance` (`.get("is_root")` de un MagicMock es truthy)
    # y el test pasaría en verde sin ejercitar el filtro de clearance.
    mock_conn.fetchrow.return_value = None
    # [0]=D-08 gate (USUA_PERM_EXPEDIENTE granted), [1]=COUNT(*) del listado.
    mock_conn.fetchval.side_effect = [True, 2]
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

    # El COUNT (X-Total-Count) y la query de página comparten el MISMO
    # predicado de clearance con el MISMO parámetro ligado (PUBLICA=1, no
    # CLASIFICADA=3) — de lo contrario el filtro no se estaría ejercitando.
    count_sql, count_clearance = mock_conn.fetchval.call_args_list[1][0]
    assert "nivel_seguridad <= $1" in count_sql
    assert count_clearance == 1

    page_args = mock_conn.fetch.call_args[0]
    assert "nivel_seguridad <= $1" in page_args[0]
    assert page_args[1] == 1


@pytest.mark.asyncio
async def test_list_expedientes_filters_by_clearance(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """RF-SEG-08 / RF-BUS-04: un llamante con clearance PUBLICA (1) —
    `resolve_caller_clearance` sin membresías/is_root — emite el listado con
    `nivel_seguridad <= $1` ligado a 1, no a CLASIFICADA (3)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None  # sin membresías → PUBLICA (1)
    mock_conn.fetchval.side_effect = [True, 0]  # [0] gate D-08, [1] COUNT
    mock_conn.fetch.return_value = []

    response = await client.get("/api/v1/expedientes", headers=TENANT_HEADERS)

    assert response.status_code == 200
    count_sql, count_clearance = mock_conn.fetchval.call_args_list[1][0]
    assert "nivel_seguridad <= $1" in count_sql
    assert count_clearance == 1

    page_args = mock_conn.fetch.call_args[0]
    assert "nivel_seguridad <= $1" in page_args[0]
    assert page_args[1] == 1


@pytest.mark.asyncio
async def test_list_expedientes_total_count_shares_clearance_filter_with_page(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """CRÍTICO (RF-SEG-08): `X-Total-Count` (COUNT(*)) debe compartir EXACTAMENTE
    el mismo predicado y parámetro de clearance que la query de página. Un fix
    que solo filtrara la página y dejara el total sin filtrar seguiría siendo un
    leak — revelaría cuántos expedientes RESERVADA/CLASIFICADA existen aunque
    ninguno aparezca listado. Usa un clearance no trivial (RESERVADA=2, ni
    PUBLICA(1) ni CLASIFICADA(3)) para no poder acertar por casualidad."""
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = {"is_root": False, "max_level": 2}
    mock_conn.fetchval.side_effect = [True, 5]  # [0] gate D-08, [1] COUNT
    mock_conn.fetch.return_value = []

    response = await client.get("/api/v1/expedientes", headers=TENANT_HEADERS)

    assert response.status_code == 200
    assert response.headers["x-total-count"] == "5"

    count_sql, count_clearance = mock_conn.fetchval.call_args_list[1][0]
    page_sql, page_clearance = mock_conn.fetch.call_args[0][:2]

    assert count_clearance == 2
    assert page_clearance == 2
    assert count_clearance == page_clearance
    assert "nivel_seguridad <= $1" in count_sql
    assert "nivel_seguridad <= $1" in page_sql


@pytest.mark.asyncio
async def test_list_expedientes_sin_user_id_es_rechazado_por_el_gate_rbac(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Sin X-User-Id, `require_permission` (D-08) YA responde 403 fail-closed
    (`check_permission` con `user_id=None` devuelve `False` sin consultar la
    BD) — la petición ni siquiera llega al no-read-up de `list_expedientes`/
    `resolve_caller_clearance`. Comportamiento REAL observado (no el asumido de
    "degrada a PUBLICA y lista igual"): fail-closed en la capa RBAC, más
    estricto todavía que el no-read-up del servicio."""
    client, mock_conn = client_with_conn

    response = await client.get(
        "/api/v1/expedientes",
        headers={"X-Tenant-Slug": TENANT_SLUG},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()


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
# PATCH /api/v1/expedientes/{expediente_id} — metadatos (NO transiciona status)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status_value", ["closed", "transferred", "open", None])
async def test_update_expediente_rechaza_status(
    client_with_conn: tuple[AsyncClient, AsyncMock],
    status_value: str | None,
) -> None:
    """`ExpedienteUpdate` retiró `status` (extra="forbid"): el PATCH ya NO es una
    ruta alterna a /close y /transfer. CUALQUIER valor de `status` en el body
    — incluido `null` — se rechaza con 422 ANTES de tocar la base de datos: es
    oracle-free (no revela existencia/clearance del expediente)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted

    response = await client.patch(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}",
        json={"status": status_value},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail[0]["type"] == "extra_forbidden"
    assert detail[0]["loc"] == ["body", "status"]
    # Prueba ejecutable de que no hay oráculo de existencia: el rechazo ocurre
    # en la validación del body de FastAPI, antes de `caller_clearance` y de
    # `get_expediente_by_id`.
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_update_expediente_status_rechazado_incluso_con_crud_3(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Fija la invariante nueva: la puerta contra transicionar por PATCH ya no es
    RBAC, es el esquema — ni siquiera un llamante con crud=5 (permiso pleno)
    puede colar `status` en el body."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # permiso pleno (crud>=3 incluido)

    response = await client.patch(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}",
        json={"status": "transferred"},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "extra_forbidden"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_update_expediente_above_clearance_returns_404_not_403(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """RF-SEG-08 no-read-up: un expediente por encima del clearance del llamante
    responde 404 (no 403) — no revela su existencia. `get_expediente_by_id`
    filtra `nivel_seguridad <= max_clearance`; con el llamante en PUBLICA (1) y
    el expediente por encima, la query real no devolvería fila — se simula
    aquí con `fetchrow → None` en la 2.ª llamada (existencia)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    # 1st fetchrow → resolve_caller_clearance → None (sin membresías) → PUBLICA(1)
    # 2nd fetchrow → get_expediente_by_id — filtrado por clearance → None
    mock_conn.fetchrow.side_effect = [None, None]

    response = await client.patch(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}",
        json={"name": "Nuevo nombre"},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "expediente_not_found"


@pytest.mark.asyncio
async def test_update_expediente_within_clearance_returns_200(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Contraparte del test anterior: con clearance suficiente (root → CLASIFICADA)
    para ver el expediente, el PATCH de un campo no-disposición (name) procede
    normalmente, deja asiento `archive.expediente_updated` y devuelve 200."""
    client, mock_conn = client_with_conn
    existing_record = make_expediente_record(exp_id=EXPEDIENTE_ID, nivel_seguridad=3)
    updated_record = make_expediente_record(
        exp_id=EXPEDIENTE_ID, nivel_seguridad=3, name="Nuevo nombre"
    )
    update_row = make_expediente_update_row(old=existing_record, new=updated_record)

    mock_conn.fetchval.return_value = True  # D-08 gate + audit.append prev_hash lookup
    # 1st fetchrow → resolve_caller_clearance → is_root → CLASIFICADA (3)
    # 2nd fetchrow → get_expediente_by_id (visible: 3 <= 3)
    # 3rd fetchrow → repo.update_expediente RETURNING (e.* + old_*)
    mock_conn.fetchrow.side_effect = [
        {"is_root": True, "max_level": None},
        existing_record,
        update_row,
    ]
    mock_conn.fetch.return_value = []

    response = await client.patch(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}",
        json={"name": "Nuevo nombre"},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Nuevo nombre"


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

    # fetchrow: get_expediente, caller_clearance (M2, RF-SEG-08 — None → PUBLICA(1),
    # suficiente porque el expediente es nivel_seguridad=1 por defecto), link
    # INSERT..ON CONFLICT RETURNING, [regenerate: get_expediente, get_current(None),
    # insert_version índice]
    mock_conn.fetchrow.side_effect = [exp_record, None, link_record, exp_record, None, exp_record]
    # fetch → radicados (empty during get_expediente check)
    mock_conn.fetch.return_value = []
    # fetchval: [0]=D-08 gate (USUA_PERM_EXPEDIENTE granted), [1]=COUNT(*) duplicate
    # check (estado='vinculado') → 0, [2]=MAX(orden_documental)+1 (E15 perfil v3,
    # asignación atómica del orden ESTABLE), [3]=audit.append prev_hash lookup
    # (regenerate_index → índice E15, ADR-010), [4]=audit.append prev_hash lookup
    # (archive.radicado_linked, M2 — la mutación SIEMPRE audita)
    mock_conn.fetchval.side_effect = [True, 0, 1, None, None]

    response = await client.post(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados",
        json={"radicado_id": str(RADICADO_ID), "tracking_number": TRACKING_NUMBER},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["radicado_id"] == str(RADICADO_ID)
    assert body["tracking_number"] == TRACKING_NUMBER
    # Llamante con clearance suficiente (M2): `added_at` real, no redactado.
    assert body["added_at"] is not None


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
    # fetchrow: get_expediente (guard), caller_clearance (M2, RF-SEG-08 — None →
    # PUBLICA(1), suficiente porque el expediente es nivel_seguridad=1 por
    # defecto), [regenerate: get_expediente, get_current(None), insert_version
    # índice]
    mock_conn.fetchrow.side_effect = [exp_record, None, exp_record, None, exp_record]
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
    # fetchrow: get_expediente (guard), caller_clearance (M2 — None → PUBLICA(1)),
    # [regenerate: get_expediente, get_current(None), insert_version índice]
    mock_conn.fetchrow.side_effect = [exp_record, None, exp_record, None, exp_record]
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

    """`/health` es VIVACIDAD: gobierna los reinicios del contenedor.

    Antes tomaba una conexión del pool en cada sondeo (cada 15 s) para preguntar
    `SELECT 1`, metiendo una dependencia externa en la sonda que decide si el
    contenedor se reinicia. Reiniciar por un Postgres caído no arregla Postgres.
    El diagnóstico de dependencias vive ahora en `/health/ready`.
    """

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "archive-service"}


@pytest.mark.asyncio
async def test_link_radicados_batch(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Inclusión masiva: vincula los nuevos, omite los ya vinculados, regenera índice 1 vez."""
    client, mock_conn = client_with_conn
    exp = make_expediente_record(exp_id=EXPEDIENTE_ID, exp_status="open")
    r1, r2 = uuid.uuid4(), uuid.uuid4()
    # get_expediente_by_id (fetchrow exp) ; caller_clearance (M2, RF-SEG-08 — None →
    # PUBLICA(1), suficiente porque el expediente es nivel_seguridad=1 por defecto);
    # luego por cada radicado: link INSERT (fetchrow); al final regenerate:
    # get_expediente_by_id (fetchrow exp), get_current (None), insert índice
    mock_conn.fetchrow.side_effect = [
        exp,  # _expediente_or_404
        None,  # caller_clearance → PUBLICA(1)
        make_radicado_link_record(radicado_id=r1),  # link r1 INSERT..ON CONFLICT RETURNING
        make_radicado_link_record(radicado_id=r2),  # link r2 INSERT..ON CONFLICT RETURNING
        exp,
        None,
        exp,  # regenerate (get_exp, get_current, insert)
    ]
    mock_conn.fetch.return_value = []
    # fetchval: [0]=D-08 gate, [1]=radicado_link_exists r1 → 0, [2]=MAX(orden_documental)+1
    # para r1 (E15 perfil v3), [3]=radicado_link_exists r2 → 0, [4]=MAX(orden_documental)+1
    # para r2, [5]=audit.append prev_hash lookup (regenerate_index, 1 sola vez tras el batch),
    # [6]=audit.append prev_hash lookup (archive.radicado_batch_linked, M2 — la
    # mutación SIEMPRE audita)
    mock_conn.fetchval.side_effect = [True, 0, 1, 0, 2, None, None]

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
# M2 (RF-SEG-08, resuelto) — redacción de RESPUESTA vs mutación SIEMPRE
# ejecutada, para link/batch/unlink/rebuild sobre un expediente CLASIFICADA
# (nivel_seguridad=3). Cada endpoint tiene el PAR: clearance insuficiente
# (PUBLICA=1, respuesta acotada) vs clearance suficiente (CLASIFICADA=3,
# respuesta completa) — ver `ArchiveService.link_radicado` /
# `link_radicados_batch` / `unlink_radicado` y `routers/indice.py::rebuild_indice`.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_link_radicados_batch_redacted_when_caller_cannot_read_classified(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """M2: con clearance PUBLICA (1) sobre un expediente CLASIFICADA (3), la
    inclusión masiva SIGUE ejecutándose (2 INSERTs + su propio asiento de
    audit_log con los conteos REALES) pero la RESPUESTA se acota a
    `vinculados=None, omitidos=None` — solo `total` (eco del input) se
    conserva. Si alguien redactara la MUTACIÓN en vez de la respuesta, los
    dos INSERTs y el asiento `archive.radicado_batch_linked` desaparecerían."""
    client, mock_conn = client_with_conn
    exp = make_expediente_record(exp_id=EXPEDIENTE_ID, exp_status="open", nivel_seguridad=3)
    r1, r2 = uuid.uuid4(), uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        exp,  # _expediente_or_404-like: get_expediente_by_id (existing)
        None,  # caller_clearance → PUBLICA(1): resolve_caller_clearance sin fila
        make_radicado_link_record(radicado_id=r1),
        make_radicado_link_record(radicado_id=r2),
        exp,  # regenerate: get_expediente_by_id
        None,  # regenerate: get_current
        exp,  # regenerate: insert índice version
    ]
    mock_conn.fetch.return_value = []
    mock_conn.fetchval.side_effect = [True, 0, 1, 0, 2, None, None]

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
    assert body["vinculados"] is None
    assert body["omitidos"] is None
    assert body["total"] == 2

    # La mutación SIEMPRE ocurre: ambos INSERTs se ejecutaron.
    insert_calls = [
        c.args
        for c in mock_conn.fetchrow.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO expediente_radicados" in c.args[0]
    ]
    assert len(insert_calls) == 2

    # La traza forense NUNCA se redacta: el audit_log lleva los conteos reales.
    calls = _audit_inserts(mock_conn, "archive.radicado_batch_linked")
    assert len(calls) == 1
    payload = json.loads(calls[0][8])
    assert payload["vinculados"] == 2
    assert payload["omitidos"] == 0


@pytest.mark.asyncio
async def test_link_radicados_batch_full_response_when_caller_can_read_classified(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """M2 (contraparte): con clearance CLASIFICADA (3) — suficiente para leer
    el mismo expediente — la respuesta trae los conteos REALES, no None."""
    client, mock_conn = client_with_conn
    exp = make_expediente_record(exp_id=EXPEDIENTE_ID, exp_status="open", nivel_seguridad=3)
    r1, r2 = uuid.uuid4(), uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        exp,
        {"is_root": True, "max_level": None},  # caller_clearance → CLASIFICADA(3)
        make_radicado_link_record(radicado_id=r1),
        make_radicado_link_record(radicado_id=r2),
        exp,
        None,
        exp,
    ]
    mock_conn.fetch.return_value = []
    mock_conn.fetchval.side_effect = [True, 0, 1, 0, 2, None, None]

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
    assert body["vinculados"] == 2
    assert body["omitidos"] == 0
    assert body["total"] == 2


@pytest.mark.asyncio
async def test_link_radicado_duplicate_redacted_when_caller_cannot_read_classified(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """M2: oráculo de pertenencia suprimido — un radicado YA vinculado a un
    expediente CLASIFICADA (3) no debe filtrar (vía 409) que ya pertenecía al
    conjunto documental a un llamante con clearance PUBLICA (1). Responde 201
    idempotente-silencioso con `added_at=None`, no 409."""
    client, mock_conn = client_with_conn
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID, nivel_seguridad=3)
    # fetchrow: get_expediente_by_id (existing), caller_clearance → PUBLICA(1)
    mock_conn.fetchrow.side_effect = [exp_record, None]
    mock_conn.fetch.return_value = []
    # fetchval: [0]=D-08 gate (truthy), [1]=duplicate check → 1 (ya vinculado)
    mock_conn.fetchval.return_value = 1

    response = await client.post(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados",
        json={"radicado_id": str(RADICADO_ID), "tracking_number": TRACKING_NUMBER},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["radicado_id"] == str(RADICADO_ID)
    assert body["tracking_number"] == TRACKING_NUMBER
    assert body["added_at"] is None


@pytest.mark.asyncio
async def test_link_radicado_duplicate_conflict_when_caller_can_read_classified(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """M2 (contraparte): con clearance CLASIFICADA (3) — suficiente para leer
    el mismo expediente — el duplicado SÍ se revela como 409, como antes de M2."""
    client, mock_conn = client_with_conn
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID, nivel_seguridad=3)
    mock_conn.fetchrow.side_effect = [
        exp_record,
        {"is_root": True, "max_level": None},  # caller_clearance → CLASIFICADA(3)
    ]
    mock_conn.fetch.return_value = []
    mock_conn.fetchval.return_value = 1  # gate + duplicate check

    response = await client.post(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados",
        json={"radicado_id": str(RADICADO_ID), "tracking_number": TRACKING_NUMBER},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "radicado_already_linked"


@pytest.mark.asyncio
async def test_link_radicado_new_link_redacted_but_still_audits(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """M2: vincular un radicado NUEVO (no duplicado) a un expediente
    CLASIFICADA (3) con clearance PUBLICA (1) del llamante SIEMPRE ejecuta la
    vinculación y SIEMPRE audita (`archive.radicado_linked` con los datos
    reales) — solo la RESPUESTA se acota (`added_at=None`). La traza forense
    no se redacta."""
    client, mock_conn = client_with_conn
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID, nivel_seguridad=3)
    link_record = make_radicado_link_record(expediente_id=EXPEDIENTE_ID, radicado_id=RADICADO_ID)
    # fetchrow: get_expediente_by_id (existing), caller_clearance → PUBLICA(1),
    # link INSERT..ON CONFLICT RETURNING, [regenerate: get_expediente_by_id,
    # get_current(None), insert_version índice]
    mock_conn.fetchrow.side_effect = [exp_record, None, link_record, exp_record, None, exp_record]
    mock_conn.fetch.return_value = []
    # fetchval: [0]=D-08 gate, [1]=duplicate check → 0, [2]=MAX(orden_documental)+1,
    # [3]=audit prev_hash (regenerate_index), [4]=audit prev_hash (radicado_linked)
    mock_conn.fetchval.side_effect = [True, 0, 1, None, None]

    response = await client.post(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados",
        json={"radicado_id": str(RADICADO_ID), "tracking_number": TRACKING_NUMBER},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    # Respuesta acotada: no revela el `added_at` real a quien no puede leer.
    assert body["added_at"] is None

    # La mutación ocurrió: el INSERT se ejecutó.
    insert_calls = [
        c.args
        for c in mock_conn.fetchrow.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO expediente_radicados" in c.args[0]
    ]
    assert len(insert_calls) == 1

    # La traza forense NUNCA se redacta: el asiento existe con datos reales.
    calls = _audit_inserts(mock_conn, "archive.radicado_linked")
    assert len(calls) == 1
    payload = json.loads(calls[0][8])
    assert payload["radicado_id"] == str(RADICADO_ID)
    assert payload["tracking_number"] == TRACKING_NUMBER


@pytest.mark.asyncio
async def test_unlink_radicado_not_found_redacted_when_caller_cannot_read_classified(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """M2: oráculo de pertenencia suprimido en `unlink` — un vínculo
    inexistente sobre un expediente CLASIFICADA (3) no debe filtrar (vía 404)
    que el radicado nunca estuvo vinculado a un llamante con clearance
    PUBLICA (1). Responde 204 exista o no el vínculo."""
    client, mock_conn = client_with_conn
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID, nivel_seguridad=3)
    mock_conn.fetchval.return_value = True  # D-08 gate
    mock_conn.execute.return_value = "UPDATE 0"  # ningún vínculo vigente coincide
    # fetchrow: get_expediente_by_id (existing), caller_clearance → PUBLICA(1)
    mock_conn.fetchrow.side_effect = [exp_record, None]
    mock_conn.fetch.return_value = []

    response = await client.delete(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados/{RADICADO_ID}",
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 204


@pytest.mark.asyncio
async def test_unlink_radicado_not_found_conflict_when_caller_can_read_classified(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """M2 (contraparte): con clearance CLASIFICADA (3) — suficiente para leer
    el mismo expediente — un vínculo inexistente SÍ se revela como 404, como
    antes de M2."""
    client, mock_conn = client_with_conn
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID, nivel_seguridad=3)
    mock_conn.fetchval.return_value = True  # D-08 gate
    mock_conn.execute.return_value = "UPDATE 0"
    mock_conn.fetchrow.side_effect = [
        exp_record,
        {"is_root": True, "max_level": None},  # caller_clearance → CLASIFICADA(3)
    ]
    mock_conn.fetch.return_value = []

    response = await client.delete(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados/{RADICADO_ID}",
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "radicado_link_not_found"


# ---------------------------------------------------------------------------
# M2 hallazgo #1 (oráculo residual de ciclo de vida, cierre) — para
# link_radicado / link_radicados_batch / unlink_radicado, `_assert_open` (409
# `expediente_not_open` con el estado LITERAL) solo se evalúa para quien SÍ
# puede leer el expediente. Un no-lector con un expediente CLASIFICADA (3)
# NO 'open' recibe el mismo ack acotado que el resto de casos SIN mutar (sin
# INSERT ni regeneración de índice) — indistinguible de open/duplicado/ya-
# excluido. Cada endpoint trae el PAR: no-lector+closed (sin 409, sin mutar)
# vs lector-legítimo+closed (409 `expediente_not_open`, el error informativo
# se conserva). Ver `ArchiveService.link_radicado`/`link_radicados_batch`/
# `unlink_radicado` y `routers/indice.py::rebuild_indice` (mismo criterio).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_link_radicado_closed_redacted_no_mutation_when_caller_cannot_read(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """M2 hallazgo #1: expediente CLASIFICADA (3) closed + clearance PUBLICA
    (1) del llamante -> NO 409 `expediente_not_open`; responde el ack
    acotado (idéntico al de open-acotado/duplicado-acotado, `added_at=None`)
    SIN mutar -- ni INSERT en `expediente_radicados` ni regeneración de
    índice."""
    client, mock_conn = client_with_conn
    exp_closed = make_expediente_record(exp_id=EXPEDIENTE_ID, exp_status="closed", nivel_seguridad=3)
    # fetchrow: get_expediente_by_id (existing, CLEARANCE_SIN_RESTRICCION),
    # caller_clearance (None: sin fila RBAC -> PUBLICA(1), insuficiente para
    # nivel_seguridad=3 -> NO puede leer -> `_assert_open` nunca se evalúa).
    mock_conn.fetchrow.side_effect = [exp_closed, None]
    mock_conn.fetch.return_value = []
    # fetchval: [0]=D-08 gate, [1]=radicado_link_exists -> 0 (no duplicado,
    # así se llega a la rama `existing.status != "open"`).
    mock_conn.fetchval.side_effect = [True, 0]

    response = await client.post(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados",
        json={"radicado_id": str(RADICADO_ID), "tracking_number": TRACKING_NUMBER},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["radicado_id"] == str(RADICADO_ID)
    assert body["tracking_number"] == TRACKING_NUMBER
    assert body["added_at"] is None

    insert_calls = [
        c.args
        for c in mock_conn.fetchrow.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO expediente_radicados" in c.args[0]
    ]
    assert len(insert_calls) == 0
    assert _audit_inserts(mock_conn, "archive.radicado_linked") == []


@pytest.mark.asyncio
async def test_link_radicado_closed_conflict_when_caller_can_read_classified(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """M2 hallazgo #1 (contraparte): con clearance CLASIFICADA (3) —
    suficiente para leer el mismo expediente closed — el 409
    `expediente_not_open` SÍ se propaga, como antes de M2."""
    client, mock_conn = client_with_conn
    exp_closed = make_expediente_record(exp_id=EXPEDIENTE_ID, exp_status="closed", nivel_seguridad=3)
    mock_conn.fetchrow.side_effect = [
        exp_closed,
        {"is_root": True, "max_level": None},  # caller_clearance → CLASIFICADA(3)
    ]
    mock_conn.fetch.return_value = []
    mock_conn.fetchval.return_value = True  # D-08 gate

    response = await client.post(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados",
        json={"radicado_id": str(RADICADO_ID), "tracking_number": TRACKING_NUMBER},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "expediente_not_open"


@pytest.mark.asyncio
async def test_link_radicados_batch_closed_redacted_no_mutation_when_caller_cannot_read(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """M2 hallazgo #1: expediente CLASIFICADA (3) closed + clearance PUBLICA
    (1) -> NO 409; responde `BatchLinkResult(vinculados=None, omitidos=None,
    total=len(items))` SIN mutar -- el bucle de vinculación nunca se
    ejecuta (ningún INSERT, ninguna regeneración de índice)."""
    client, mock_conn = client_with_conn
    exp_closed = make_expediente_record(exp_id=EXPEDIENTE_ID, exp_status="closed", nivel_seguridad=3)
    r1, r2 = uuid.uuid4(), uuid.uuid4()
    # fetchrow: get_expediente_by_id (existing), caller_clearance → PUBLICA(1)
    # (insuficiente) -> retorno anticipado, sin entrar al bucle por-item.
    mock_conn.fetchrow.side_effect = [exp_closed, None]
    mock_conn.fetch.return_value = []
    mock_conn.fetchval.return_value = True  # D-08 gate (único fetchval esperado)

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
    assert body["vinculados"] is None
    assert body["omitidos"] is None
    assert body["total"] == 2

    insert_calls = [
        c.args
        for c in mock_conn.fetchrow.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO expediente_radicados" in c.args[0]
    ]
    assert len(insert_calls) == 0
    assert _audit_inserts(mock_conn, "archive.radicado_batch_linked") == []


@pytest.mark.asyncio
async def test_link_radicados_batch_closed_conflict_when_caller_can_read_classified(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """M2 hallazgo #1 (contraparte): con clearance CLASIFICADA (3) — el 409
    `expediente_not_open` SÍ se propaga para la inclusión masiva."""
    client, mock_conn = client_with_conn
    exp_closed = make_expediente_record(exp_id=EXPEDIENTE_ID, exp_status="closed", nivel_seguridad=3)
    r1, r2 = uuid.uuid4(), uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        exp_closed,
        {"is_root": True, "max_level": None},  # caller_clearance → CLASIFICADA(3)
    ]
    mock_conn.fetch.return_value = []
    mock_conn.fetchval.return_value = True  # D-08 gate

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
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "expediente_not_open"


@pytest.mark.asyncio
async def test_unlink_radicado_closed_no_mutation_when_caller_cannot_read_classified(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """M2 hallazgo #1: expediente CLASIFICADA (3) closed + clearance PUBLICA
    (1) -> NO 409 `expediente_not_open`; responde 204 SIN mutar -- ningún
    UPDATE de exclusión sobre `expediente_radicados`."""
    client, mock_conn = client_with_conn
    exp_closed = make_expediente_record(exp_id=EXPEDIENTE_ID, exp_status="closed", nivel_seguridad=3)
    mock_conn.fetchval.return_value = True  # D-08 gate
    # fetchrow: get_expediente_by_id (existing), caller_clearance → PUBLICA(1)
    # (insuficiente) -> retorno anticipado, antes de `repo.unlink_radicado`.
    mock_conn.fetchrow.side_effect = [exp_closed, None]
    mock_conn.fetch.return_value = []

    response = await client.delete(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados/{RADICADO_ID}",
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 204

    exclude_calls = [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "expediente_radicados" in c.args[0] and "excluido" in c.args[0]
    ]
    assert len(exclude_calls) == 0
    assert _audit_inserts(mock_conn, "archive.radicado_excluido") == []


@pytest.mark.asyncio
async def test_unlink_radicado_closed_conflict_when_caller_can_read_classified(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """M2 hallazgo #1 (contraparte): con clearance CLASIFICADA (3) — el 409
    `expediente_not_open` SÍ se propaga para la exclusión."""
    client, mock_conn = client_with_conn
    exp_closed = make_expediente_record(exp_id=EXPEDIENTE_ID, exp_status="closed", nivel_seguridad=3)
    mock_conn.fetchval.return_value = True  # D-08 gate
    mock_conn.fetchrow.side_effect = [
        exp_closed,
        {"is_root": True, "max_level": None},  # caller_clearance → CLASIFICADA(3)
    ]
    mock_conn.fetch.return_value = []

    response = await client.delete(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados/{RADICADO_ID}",
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "expediente_not_open"


# ---------------------------------------------------------------------------
# Atomicidad vínculo<->auditoría (link_radicado / link_radicados_batch /
# unlink_radicado): las escrituras (mutación + `_regenerate_index` + evento de
# la hoja de ruta + `audit.append` propio) están envueltas en `async with
# self._conn.transaction():` (mismo patrón que `create_expediente`, ver esa
# sección arriba). Con `conn` mockeado no se puede aseverar el ROLLBACK físico
# — lo que sí se cubre aquí es la ESTRUCTURA (el wrap se abre SOLO en el
# camino que muta, NUNCA en las ramas de redacción M2 / M2 hallazgo #1 que no
# mutan) y el COMPORTAMIENTO observable con mocks (un fallo a mitad de la
# secuencia envuelta se PROPAGA, no hay éxito a medias).
#
# `IndexService.regenerate` dentro de `_regenerate_index` deja SIEMPRE su
# propio asiento `archive.indice_regenerated` (además de `insert_version`),
# y `ArchiveRepository.link_radicado` abre SU PROPIA (sub)transacción para el
# `pg_advisory_xact_lock` del orden_documental atómico — así que un
# `link_radicado` exitoso abre `conn.transaction()` 5 veces: el wrap propio +
# la del repo (advisory lock) + `insert_version` + `audit.append`
# (indice_regenerated) + `audit.append` (radicado_linked). `unlink_radicado`
# no tiene esa (sub)transacción extra del repo (su mutación es un UPDATE
# simple vía `execute`, sin advisory lock) — 4 veces: wrap + insert_version +
# audit.append (indice_regenerated) + audit.append (radicado_excluido).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_link_radicado_wraps_writes_in_a_transaction(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Anti-regresión del wrapping: si alguien quita el `async with
    self._conn.transaction():` de `link_radicado`, este test debe fallar (el
    conteo caería de 5 a 4). Ver el comentario de sección para el desglose de
    las 5 (sub)transacciones."""
    client, mock_conn = client_with_conn
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID)
    link_record = make_radicado_link_record(expediente_id=EXPEDIENTE_ID, radicado_id=RADICADO_ID)
    mock_conn.fetchrow.side_effect = [exp_record, None, link_record, exp_record, None, exp_record]
    mock_conn.fetch.return_value = []
    mock_conn.fetchval.side_effect = [True, 0, 1, None, None]

    response = await client.post(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados",
        json={"radicado_id": str(RADICADO_ID), "tracking_number": TRACKING_NUMBER},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    assert mock_conn.transaction.called
    assert mock_conn.transaction.call_count == 5


@pytest.mark.asyncio
async def test_link_radicado_redacted_duplicate_never_opens_a_transaction(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """M2: un llamante sin clearance de lectura sobre un expediente
    CLASIFICADA (3) que intenta vincular un radicado YA vinculado recibe el
    ack idempotente-silencioso (`added_at=None`, sin 409) SIN abrir
    transacción — es una rama que no muta nada."""
    client, mock_conn = client_with_conn
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID, nivel_seguridad=3)
    mock_conn.fetchrow.side_effect = [exp_record, None]
    mock_conn.fetch.return_value = []
    mock_conn.fetchval.return_value = 1  # gate + duplicate check → ya vinculado

    response = await client.post(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados",
        json={"radicado_id": str(RADICADO_ID), "tracking_number": TRACKING_NUMBER},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    assert response.json()["added_at"] is None
    assert mock_conn.transaction.call_count == 0


@pytest.mark.asyncio
async def test_link_radicado_redacted_closed_never_opens_a_transaction(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """M2 hallazgo #1: un llamante sin clearance de lectura sobre un
    expediente CLASIFICADA (3) *closed* recibe el mismo ack acotado SIN
    mutar — `_assert_open` nunca se evalúa y ninguna transacción se abre."""
    client, mock_conn = client_with_conn
    exp_closed = make_expediente_record(exp_id=EXPEDIENTE_ID, exp_status="closed", nivel_seguridad=3)
    mock_conn.fetchrow.side_effect = [exp_closed, None]
    mock_conn.fetch.return_value = []
    mock_conn.fetchval.side_effect = [True, 0]  # gate; duplicate check → no duplicado

    response = await client.post(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados",
        json={"radicado_id": str(RADICADO_ID), "tracking_number": TRACKING_NUMBER},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    assert response.json()["added_at"] is None
    assert mock_conn.transaction.call_count == 0


@pytest.mark.asyncio
async def test_link_radicado_late_failure_propagates_not_a_fake_success(
    client_with_conn: tuple[AsyncClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un fallo TARDÍO dentro de la transacción (aquí: `audit.append`,
    invocado primero por `IndexService.regenerate` para su propio asiento
    `archive.indice_regenerated`) debe PROPAGARSE — `link_radicado` nunca
    debe devolver un `ExpedienteRadicadoResponse` de éxito si el asiento
    forense no se pudo escribir. Sustituye `audit.append` por completo (mismo
    patrón que `test_create_expediente_late_failure_propagates_not_a_fake_success`)
    e invoca `ArchiveService.link_radicado` DIRECTAMENTE (no vía HTTP) — la
    aserción relevante es que la excepción sale de la corrutina del
    SERVICIO."""
    _, mock_conn = client_with_conn
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID)
    link_record = make_radicado_link_record(expediente_id=EXPEDIENTE_ID, radicado_id=RADICADO_ID)
    # fetchrow: get_expediente_by_id (link_radicado), caller_clearance (None
    # → PUBLICA), link INSERT..ON CONFLICT RETURNING, [regenerate:
    # get_expediente_by_id, get_current(None), insert_version índice]
    mock_conn.fetchrow.side_effect = [exp_record, None, link_record, exp_record, None, exp_record]
    mock_conn.fetch.return_value = []
    # fetchval: [0]=duplicate check → 0, [1]=MAX(orden_documental)+1. Sin gate
    # D-08 (llamada directa al servicio, sin pasar por el router/dependency).
    mock_conn.fetchval.side_effect = [0, 1]

    async def _boom(*args: object, **kwargs: object) -> str:
        raise RuntimeError("audit failure (simulated)")

    monkeypatch.setattr(archive_service_module.audit, "append", _boom)

    service = archive_service_module.ArchiveService(mock_conn, TENANT_SLUG)
    payload = RadicadoLink(radicado_id=RADICADO_ID, tracking_number=TRACKING_NUMBER)

    with pytest.raises(RuntimeError, match="audit failure"):
        await service.link_radicado(EXPEDIENTE_ID, payload, USER_ID)

    # Llegó a mutar (el INSERT del vínculo y el de la versión del índice ya
    # se ejecutaron) antes de que `audit.append` reventara — el fallo es
    # TARDÍO, no previo.
    link_insert_calls = [
        c.args
        for c in mock_conn.fetchrow.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO expediente_radicados" in c.args[0]
    ]
    assert link_insert_calls != []


@pytest.mark.asyncio
async def test_link_radicados_batch_wraps_the_whole_loop_in_one_transaction(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Anti-regresión: `link_radicados_batch` abre el wrap propio (el `async
    with` que envuelve el `for` completo) UNA sola vez para todo el lote — no
    una por vínculo. El conteo TOTAL sí escala con N porque cada
    `ArchiveRepository.link_radicado` abre su propia (sub)transacción para el
    advisory lock del orden_documental: con N=2 ítems nuevos, se esperan 6 =
    1 (wrap del lote) + 2 (una por ítem, advisory lock) + 1 (insert_version)
    + 1 (audit.append indice_regenerated) + 1 (audit.append
    radicado_batch_linked). Si el wrap del LOTE desapareciera pero cada ítem
    conservara su propia sub-transacción, el conteo bajaría a 5, no a 6 — así
    que esta cifra por sí sola no distingue "wrap del lote ausente" de "wrap
    presente"; lo que SÍ es anti-regresión aquí es que el conteo NO escala
    1:1 con N (ver docstring de la clase para el desglose)."""
    client, mock_conn = client_with_conn
    exp = make_expediente_record(exp_id=EXPEDIENTE_ID, exp_status="open")
    r1, r2 = uuid.uuid4(), uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        exp,
        None,
        make_radicado_link_record(radicado_id=r1),
        make_radicado_link_record(radicado_id=r2),
        exp,
        None,
        exp,
    ]
    mock_conn.fetch.return_value = []
    mock_conn.fetchval.side_effect = [True, 0, 1, 0, 2, None, None]

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
    assert mock_conn.transaction.call_count == 6


@pytest.mark.asyncio
async def test_link_radicados_batch_all_omitted_no_audit_no_regen(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Lote donde TODOS los ítems ya estaban vinculados (`vinculados == 0`):
    el wrap se abre igual (envuelve el bucle completo) pero, al no haber
    nada que vincular, ni `_regenerate_index` ni `audit.append` se invocan
    dentro — 0 asientos `archive.radicado_batch_linked`."""
    client, mock_conn = client_with_conn
    exp = make_expediente_record(exp_id=EXPEDIENTE_ID, exp_status="open")
    r1, r2 = uuid.uuid4(), uuid.uuid4()
    mock_conn.fetchrow.side_effect = [exp, None]  # get_expediente_by_id; caller_clearance
    mock_conn.fetch.return_value = []
    # fetchval: [0]=D-08 gate, [1]=radicado_link_exists r1 → True (ya vinculado),
    # [2]=radicado_link_exists r2 → True
    mock_conn.fetchval.side_effect = [True, 1, 1]

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
    assert body["vinculados"] == 0
    assert body["omitidos"] == 2

    assert mock_conn.transaction.call_count == 1  # solo el wrap del bucle
    assert _audit_inserts(mock_conn, "archive.radicado_batch_linked") == []


@pytest.mark.asyncio
async def test_unlink_radicado_wraps_writes_in_a_transaction(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Anti-regresión: una exclusión exitosa (`excluded=True`, clearance
    suficiente) invoca `conn.transaction()` 4 veces (wrap + insert_version +
    audit.append[indice_regenerated] + audit.append[radicado_excluido]) —
    mismo conteo que `link_radicado`."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08 gate
    mock_conn.execute.return_value = "UPDATE 1"  # E15 perfil v3: soft-delete
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID)
    mock_conn.fetchrow.side_effect = [exp_record, None, exp_record, None, exp_record]
    mock_conn.fetch.return_value = []

    response = await client.delete(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados/{RADICADO_ID}",
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 204
    assert mock_conn.transaction.call_count == 4


@pytest.mark.asyncio
async def test_unlink_radicado_not_found_for_reader_opens_tx_then_raises(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Un llamante que SÍ puede leer el expediente y excluye un vínculo
    inexistente recibe el 404 informativo `radicado_link_not_found` — el
    `raise` ocurre DENTRO del wrap (la tx se abrió pero no queda nada que
    revertir: ni `insert_version` ni `audit.append` llegan a invocarse)."""
    client, mock_conn = client_with_conn
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID, nivel_seguridad=3)
    mock_conn.fetchval.return_value = True  # D-08 gate
    mock_conn.execute.return_value = "UPDATE 0"
    mock_conn.fetchrow.side_effect = [
        exp_record,
        {"is_root": True, "max_level": None},  # caller_clearance → CLASIFICADA(3)
    ]
    mock_conn.fetch.return_value = []

    response = await client.delete(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados/{RADICADO_ID}",
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 404
    assert mock_conn.transaction.call_count == 1  # solo el wrap; nada dentro
    assert _audit_inserts(mock_conn, "archive.radicado_excluido") == []


@pytest.mark.asyncio
async def test_unlink_radicado_not_found_for_non_reader_silent_return_within_tx(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """M2: un llamante SIN clearance de lectura que excluye un vínculo
    inexistente recibe 204 silencioso (`return` dentro del wrap, sin
    excepción) — la tx se abre (commit vacío, inocuo) pero no hay `insert_
    version` ni `audit.append`."""
    client, mock_conn = client_with_conn
    exp_record = make_expediente_record(exp_id=EXPEDIENTE_ID, nivel_seguridad=3)
    mock_conn.fetchval.return_value = True  # D-08 gate
    mock_conn.execute.return_value = "UPDATE 0"
    mock_conn.fetchrow.side_effect = [exp_record, None]  # caller_clearance → PUBLICA(1)
    mock_conn.fetch.return_value = []

    response = await client.delete(
        f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados/{RADICADO_ID}",
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 204
    assert mock_conn.transaction.call_count == 1  # solo el wrap; nada dentro
    assert _audit_inserts(mock_conn, "archive.radicado_excluido") == []


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
