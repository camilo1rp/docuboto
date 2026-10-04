# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests de metadatos del expediente (E03, ADR-007): plantillas + validación.

Secuencia de CREATE con plantilla de serie (RF-SEG-08, M3 — no-write-up de
`nivel_seguridad` al ABRIR el expediente: `caller_clearance` corre ANTES que
cualquier otra cosa, incl. la resolución de plantilla):
  1. fetchrow — resolve_caller_clearance (gate M3: nivel_seguridad <= clearance)
  2. execute  — SET search_path (get_active_template)
  3. fetchrow — plantilla activa de la serie
  4. execute  — SET search_path + INSERT ON CONFLICT (next_expediente_code)
  5. fetchval — last_seq
  6. execute  — SET search_path (create_expediente)
  7. fetchrow — INSERT expediente RETURNING
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from tests.conftest import TENANT_HEADERS, make_expediente_record, make_trd_serie_record

_SCHEMA = {
    "type": "object",
    "required": ["contrato_num"],
    "properties": {"contrato_num": {"type": "string"}},
    "additionalProperties": False,
}


def _template_row(template_id: uuid.UUID, serie_id: uuid.UUID) -> dict:
    return {
        "id": template_id,
        "trd_serie_id": serie_id,
        "version": 1,
        "json_schema": json.dumps(_SCHEMA),  # asyncpg devuelve JSONB como str
        "activo": True,
        "created_at": datetime.now(tz=UTC),
    }


# --- CRUD de plantillas -----------------------------------------------------
@pytest.mark.asyncio
async def test_create_template(client_with_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # USUA_PERM_TRD granted (gate)
    new_id = uuid.uuid4()
    serie_id = uuid.uuid4()
    mock_conn.fetchrow.return_value = _template_row(new_id, serie_id)

    resp = await client.post(
        "/api/v1/expediente-metadata/templates",
        json={"trd_serie_id": str(serie_id), "json_schema": _SCHEMA},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["trd_serie_id"] == str(serie_id)
    assert body["json_schema"]["required"] == ["contrato_num"]


@pytest.mark.asyncio
async def test_create_template_writes_audit_entry(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Media (RBAC follow-up): crear una plantilla de metadatos deja traza en
    `audit_log` con el actor (X-User-Id) y la serie/versión de la plantilla."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # USUA_PERM_TRD granted (gate)
    new_id = uuid.uuid4()
    serie_id = uuid.uuid4()
    mock_conn.fetchrow.return_value = _template_row(new_id, serie_id)

    resp = await client.post(
        "/api/v1/expediente-metadata/templates",
        json={"trd_serie_id": str(serie_id), "json_schema": _SCHEMA},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201

    audit_calls = [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    # args: (sql, tenant, service, actor, canal, action, object_type, object_ref, payload, prev_hash, hash)
    assert len(audit_calls) == 1
    assert audit_calls[0][3] == TENANT_HEADERS["X-User-Id"]
    assert audit_calls[0][5] == "archive.metadata_template_created"
    assert audit_calls[0][6] == "metadata_template"
    assert audit_calls[0][7] == f"{serie_id}:v1"


# --- RBAC gate: USUA_PERM_TRD en create_template (metadata.py) -------------
# La lectura (GET /expediente-metadata/templates) queda abierta — sin cambio,
# solo _require_tenant (la consumen las plantillas de radicación).


@pytest.mark.asyncio
async def test_create_template_forbidden_without_permission(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Mutación representativa: sin USUA_PERM_TRD, `require_permission` corta
    con 403 antes de tocar la lógica de negocio (no se inserta la plantilla)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False  # permiso denegado
    serie_id = uuid.uuid4()

    resp = await client.post(
        "/api/v1/expediente-metadata/templates",
        json={"trd_serie_id": str(serie_id), "json_schema": _SCHEMA},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_create_template_forbidden_without_user_id(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Fail-closed sobre la IDENTIDAD: sin X-User-Id no hay usuario que
    resolver → 403, aunque la BD "tuviera" el permiso."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    serie_id = uuid.uuid4()

    resp = await client.post(
        "/api/v1/expediente-metadata/templates",
        json={"trd_serie_id": str(serie_id), "json_schema": _SCHEMA},
        headers={"X-Tenant-Slug": TENANT_HEADERS["X-Tenant-Slug"]},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_list_templates_open_read_no_user_id_needed(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Confirma que la lectura sigue ABIERTA (solo `_require_tenant`): sin
    X-User-Id ni permiso mockeado, GET /expediente-metadata/templates sigue
    respondiendo 200."""
    client, mock_conn = client_with_conn
    mock_conn.fetch.return_value = []
    mock_conn.fetchval.return_value = 0

    resp = await client.get(
        "/api/v1/expediente-metadata/templates",
        headers={"X-Tenant-Slug": TENANT_HEADERS["X-Tenant-Slug"]},
    )

    assert resp.status_code == 200


# --- validación al abrir expediente ----------------------------------------
@pytest.mark.asyncio
async def test_create_expediente_valid_metadata(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    template_id = uuid.uuid4()
    serie_id = uuid.uuid4()
    metadata = {"contrato_num": "C-2024-001"}
    exp_record = make_expediente_record(
        trd_serie_id=serie_id, metadata=metadata, metadata_template_id=template_id
    )
    # 0) resolve_caller_clearance (M3, RF-SEG-08) → None → PUBLICA(1), suficiente
    #    porque el payload no fija nivel_seguridad (default 1); 1) ADR-026
    #    (Increment 2): get_trd_serie_by_id -- valida origen<->tipo_instrumento
    #    ANTES de tocar la plantilla/la base (mismo id que el resto de la
    #    secuencia reutiliza); 2) plantilla activa, 3) INSERT expediente,
    #    4) lookup TRD serie (E15 v3, <contexto> del índice v0 — el
    #    expediente tiene trd_serie_id), 5) INSERT índice v0 (E15)
    mock_conn.fetchrow.side_effect = [
        None,
        make_trd_serie_record(serie_id=serie_id),
        _template_row(template_id, serie_id),
        exp_record,
        make_trd_serie_record(serie_id=serie_id),
        exp_record,
    ]
    mock_conn.fetchval.return_value = 1

    resp = await client.post(
        "/api/v1/expedientes",
        json={
            "name": "Contrato 2024-001",
            "trd_serie_id": str(serie_id),
            "metadata": metadata,
        },
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201
    assert resp.json()["metadata"] == metadata


@pytest.mark.asyncio
async def test_create_expediente_invalid_metadata_is_422(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    template_id = uuid.uuid4()
    serie_id = uuid.uuid4()
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    # Falta 'contrato_num' (requerido) → falla la validación antes de crear.
    # 0) resolve_caller_clearance (M3) → None → PUBLICA(1); el payload no fija
    #    nivel_seguridad (default 1), así que el gate no interfiere; 1) ADR-026
    #    (Increment 2): get_trd_serie_by_id (origen<->tipo_instrumento, pasa);
    #    2) plantilla activa -- y se llega a la validación de metadatos que
    #    este test ejercita.
    mock_conn.fetchrow.side_effect = [
        None,
        make_trd_serie_record(serie_id=serie_id),
        _template_row(template_id, serie_id),
    ]

    resp = await client.post(
        "/api/v1/expedientes",
        json={
            "name": "Contrato sin metadato",
            "trd_serie_id": str(serie_id),
            "metadata": {"otro": "valor"},
        },
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "metadata_validation_failed"
    # AREA-12-05: el sobre dice QUÉ propiedad falla, para marcar el control.
    assert resp.json()["detail"]["field"] == "contrato_num"
