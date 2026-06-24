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

Secuencia de CREATE con plantilla de serie:
  1. execute  — SET search_path (get_active_template)
  2. fetchrow — plantilla activa de la serie
  3. execute  — SET search_path + INSERT ON CONFLICT (next_expediente_code)
  4. fetchval — last_seq
  5. execute  — SET search_path (create_expediente)
  6. fetchrow — INSERT expediente RETURNING
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from tests.conftest import TENANT_HEADERS, make_expediente_record

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
    # 1) plantilla activa, 2) INSERT expediente, 3) INSERT índice v0 (E15)
    mock_conn.fetchrow.side_effect = [_template_row(template_id, serie_id), exp_record, exp_record]
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
    # Falta 'contrato_num' (requerido) → falla la validación antes de crear.
    mock_conn.fetchrow.side_effect = [_template_row(template_id, serie_id)]

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
