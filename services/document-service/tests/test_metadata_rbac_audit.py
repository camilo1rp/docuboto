# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""RBAC + auditoría en el catálogo de metadatos (E03, ADR-007/RF-MET-03).

Hallazgo Alto de auditoría de seguridad: `POST /metadata/templates` y las tres
escrituras de `metadata_elements` (POST/PATCH/DELETE) no tenían NINGÚN
`require_permission`, mientras que su gemelo exacto en archive-service
(`POST /expediente-metadata/templates`) sí exige `USUA_PERM_TRD`. Como los
radicados se validan contra la plantilla ACTIVA, cualquier usuario autenticado
podía anular la validación de metadatos obligatorios de un tipo documental
completo. Cubre:

  (a) las cuatro escrituras exigen `USUA_PERM_TRD` (403 sin él, éxito con él).
  (b) `DELETE /metadata/elements/{id}` exige además `min_crud=3` (precedente
      `archive-service/app/routers/tipos_documentales.py::delete_tipo`).
  (c) las cuatro escrituras dejan un asiento en `public.audit_log` (Media:
      antes no auditaban en absoluto).

Espejo de `tests/test_signatures.py::test_firmas_exigen_perm_firma` (403
fail-closed vía `fetchval.return_value = None`) y de
`tests/test_anulacion_audit.py` (inspección del INSERT crudo contra la
conexión mockeada).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from tests.conftest import TENANT_HEADERS

_USER = str(uuid.uuid4())
_HEADERS = {**TENANT_HEADERS, "X-User-Id": _USER}

_SCHEMA = {
    "type": "object",
    "required": ["area"],
    "properties": {"area": {"type": "string"}},
}


def _template_row(template_id: uuid.UUID) -> dict:
    return {
        "id": template_id,
        "tipo_documental": "Factura",
        "version": 1,
        "json_schema": json.dumps(_SCHEMA),
        "activo": True,
        "created_at": datetime.now(tz=UTC),
    }


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


def _audit_insert_calls(mock_conn) -> list:
    return [
        c
        for c in mock_conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]


# ---------------------------------------------------------------------------
# (a)/(b) USUA_PERM_TRD (403 sin él, éxito con él) + min_crud del DELETE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_template_sin_permiso_403(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None  # sin fila en auth_users → fail-closed

    resp = await client.post(
        "/api/v1/metadata/templates",
        json={"tipo_documental": "Factura", "json_schema": _SCHEMA},
        headers=_HEADERS,
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_create_template_con_permiso_201_y_audita(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    new_id = uuid.uuid4()
    mock_conn.fetchval.return_value = True  # USUA_PERM_TRD concedido
    mock_conn.fetchrow.return_value = _template_row(new_id)

    resp = await client.post(
        "/api/v1/metadata/templates",
        json={"tipo_documental": "Factura", "json_schema": _SCHEMA},
        headers=_HEADERS,
    )

    assert resp.status_code == 201
    inserts = _audit_insert_calls(mock_conn)
    assert len(inserts) == 1
    args = inserts[0].args
    assert args[1] == TENANT_HEADERS["X-Tenant-Slug"]
    assert args[2] == "document-service"
    assert args[3] == _USER
    assert args[5] == "document.metadata_template_creada"
    assert args[6] == "metadata_template"
    assert args[7] == str(new_id)
    payload = json.loads(args[8])
    assert payload["tipo_documental"] == "Factura"


@pytest.mark.asyncio
async def test_create_element_sin_permiso_403(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None

    resp = await client.post(
        "/api/v1/metadata/elements",
        json={"clave": "area", "etiqueta": "Área", "tipo_dato": "select"},
        headers=_HEADERS,
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_create_element_con_permiso_201_y_audita(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    eid = uuid.uuid4()
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = _element_row(eid)

    resp = await client.post(
        "/api/v1/metadata/elements",
        json={"clave": "area", "etiqueta": "Área", "tipo_dato": "select"},
        headers=_HEADERS,
    )

    assert resp.status_code == 201
    inserts = _audit_insert_calls(mock_conn)
    assert len(inserts) == 1
    args = inserts[0].args
    assert args[3] == _USER
    assert args[5] == "document.metadata_element_creado"
    assert args[6] == "metadata_element"
    assert args[7] == str(eid)


@pytest.mark.asyncio
async def test_update_element_sin_permiso_403(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None

    resp = await client.patch(
        f"/api/v1/metadata/elements/{uuid.uuid4()}",
        json={"etiqueta": "Nueva etiqueta"},
        headers=_HEADERS,
    )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_update_element_con_permiso_200_y_audita(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    eid = uuid.uuid4()
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = _element_row(eid, etiqueta="Nueva etiqueta")

    resp = await client.patch(
        f"/api/v1/metadata/elements/{eid}",
        json={"etiqueta": "Nueva etiqueta"},
        headers=_HEADERS,
    )

    assert resp.status_code == 200
    inserts = _audit_insert_calls(mock_conn)
    assert len(inserts) == 1
    args = inserts[0].args
    assert args[5] == "document.metadata_element_actualizado"
    assert args[6] == "metadata_element"
    assert args[7] == str(eid)


@pytest.mark.asyncio
async def test_delete_element_sin_permiso_403(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None

    resp = await client.delete(f"/api/v1/metadata/elements/{uuid.uuid4()}", headers=_HEADERS)

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_delete_element_exige_min_crud_3(client_with_conn) -> None:
    """El gate del DELETE pide min_crud=3 (RF-SEG-03), no el 1 por defecto de
    create/update — se verifica el parámetro tal cual llega a la consulta RBAC
    (mismo criterio que `check_permission`, que usa `gp.crud >= $3`)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.execute.return_value = "DELETE 1"

    await client.delete(f"/api/v1/metadata/elements/{uuid.uuid4()}", headers=_HEADERS)

    rbac_calls = [
        c
        for c in mock_conn.fetchval.call_args_list
        if c.args and "auth_permissions" in c.args[0]
    ]
    assert len(rbac_calls) == 1
    assert rbac_calls[0].args[-1] == 3  # min_crud


@pytest.mark.asyncio
async def test_delete_element_con_permiso_204_y_audita(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    eid = uuid.uuid4()
    mock_conn.fetchval.return_value = True
    mock_conn.execute.return_value = "DELETE 1"

    resp = await client.delete(f"/api/v1/metadata/elements/{eid}", headers=_HEADERS)

    assert resp.status_code == 204
    inserts = _audit_insert_calls(mock_conn)
    assert len(inserts) == 1
    args = inserts[0].args
    assert args[5] == "document.metadata_element_eliminado"
    assert args[6] == "metadata_element"
    assert args[7] == str(eid)


@pytest.mark.asyncio
async def test_delete_element_not_found_no_audita(client_with_conn) -> None:
    """`DELETE result.endswith("1")` en 0 filas → 404 y SIN asiento (nada mutó)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.execute.return_value = "DELETE 0"

    resp = await client.delete(f"/api/v1/metadata/elements/{uuid.uuid4()}", headers=_HEADERS)

    assert resp.status_code == 404
    assert _audit_insert_calls(mock_conn) == []
