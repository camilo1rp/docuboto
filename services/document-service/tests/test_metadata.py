# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests de metadatos (E03, ADR-007): plantillas + validación en la radicación.

La conexión está mockeada. Secuencia de CREATE con metadatos:
  1. fetchrow — get_active_template (plantilla activa por doc_class)
  2. fetchrow — SELECT code FROM public.tenants
  3. fetchval — next_sequence
  4. fetchrow — INSERT radicado RETURNING
  5. fetch    — anexos
"""

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from tests.conftest import TENANT_HEADERS, make_radicado_record

_PAYLOAD = {
    "doc_type": "E",
    "subject": "Factura de servicios",
    "doc_class": "Factura",
    "dest_dept_code": 1,
    "pages": 1,
}

# JSON Schema de ejemplo: requiere 'area' (string) y 'valor' (número).
_SCHEMA = {
    "type": "object",
    "required": ["area", "valor"],
    "properties": {
        "area": {"type": "string"},
        "valor": {"type": "number"},
    },
    "additionalProperties": False,
}


def _template_row(template_id: uuid.UUID) -> dict:
    # asyncpg devuelve JSONB como str; el servicio tolera str o dict.
    import json

    return {
        "id": template_id,
        "tipo_documental": "Factura",
        "version": 1,
        "json_schema": json.dumps(_SCHEMA),
        "activo": True,
        "created_at": datetime.now(tz=UTC),
    }


# --- CRUD de plantillas -----------------------------------------------------
@pytest.mark.asyncio
async def test_create_template(client_with_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, mock_conn = client_with_conn
    new_id = uuid.uuid4()
    mock_conn.fetchrow.return_value = _template_row(new_id)

    resp = await client.post(
        "/api/v1/metadata/templates",
        json={"tipo_documental": "Factura", "json_schema": _SCHEMA},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["tipo_documental"] == "Factura"
    assert body["json_schema"]["required"] == ["area", "valor"]
    assert body["id"] == str(new_id)


@pytest.mark.asyncio
async def test_create_template_rejects_bad_schema(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    # 'type' inválido → no es un JSON Schema válido.
    resp = await client.post(
        "/api/v1/metadata/templates",
        json={"tipo_documental": "Factura", "json_schema": {"type": "not-a-type"}},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "invalid_json_schema"


# --- catálogo de elementos de metadato (RF-MET-03) -------------------------
def _element_row(elem_id: uuid.UUID, **kw) -> dict:
    base = {
        "id": elem_id,
        "clave": "area",
        "etiqueta": "Área responsable",
        "tipo_dato": "select",
        "longitud": None,
        "ocurrencia_min": 1,
        "ocurrencia_max": 1,
        "modificable": True,
        "valor_default": None,
        "opciones": json.dumps(["juridica", "financiera"]),
        "orden": 1,
        "searchable": True,
        "mapeo_dublin_core": "dc:coverage",
        "activo": True,
        "created_at": datetime.now(tz=UTC),
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_create_element(client_with_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, mock_conn = client_with_conn
    eid = uuid.uuid4()
    mock_conn.fetchrow.return_value = _element_row(eid)

    resp = await client.post(
        "/api/v1/metadata/elements",
        json={
            "clave": "area",
            "etiqueta": "Área responsable",
            "tipo_dato": "select",
            "opciones": ["juridica", "financiera"],
            "searchable": True,
            "mapeo_dublin_core": "dc:coverage",
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["clave"] == "area"
    assert body["opciones"] == ["juridica", "financiera"]
    assert body["mapeo_dublin_core"] == "dc:coverage"


@pytest.mark.asyncio
async def test_list_elements(client_with_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetch.return_value = [_element_row(uuid.uuid4())]
    resp = await client.get("/api/v1/metadata/elements?activo=true", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()[0]["tipo_dato"] == "select"


@pytest.mark.asyncio
async def test_get_element_not_found(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None
    resp = await client.get(f"/api/v1/metadata/elements/{uuid.uuid4()}", headers=TENANT_HEADERS)
    assert resp.status_code == 404


# --- validación en la radicación -------------------------------------------
@pytest.mark.asyncio
async def test_create_document_valid_metadata(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Con plantilla activa y metadatos válidos, se radica (201) y se conservan."""
    client, mock_conn = client_with_conn
    template_id = uuid.uuid4()
    metadata = {"area": "juridica", "valor": 1500.0}
    record = make_radicado_record(metadata=metadata, metadata_template_id=template_id)
    # 1) get_active_template, 2) tenant_code, 3) INSERT radicado
    mock_conn.fetchrow.side_effect = [_template_row(template_id), {"code": "DEMO"}, record]
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = []

    resp = await client.post(
        "/api/v1/documents",
        json={**_PAYLOAD, "metadata": metadata},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201
    assert resp.json()["metadata"] == metadata


@pytest.mark.asyncio
async def test_create_document_invalid_metadata_is_422(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Metadatos que no cumplen el JSON Schema → 422 y NO se radica."""
    client, mock_conn = client_with_conn
    template_id = uuid.uuid4()
    # Falta 'valor' (requerido) y 'area' es número en vez de string.
    mock_conn.fetchrow.side_effect = [_template_row(template_id)]

    resp = await client.post(
        "/api/v1/documents",
        json={**_PAYLOAD, "metadata": {"area": 123}},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "metadata_validation_failed"
    # AREA-12-05: el sobre nombra la propiedad que incumple (area o valor).
    assert resp.json()["detail"]["field"] in {"area", "valor"}
