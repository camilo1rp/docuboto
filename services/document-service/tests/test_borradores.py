# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de plantillas y borradores de Salida (paridad legado)."""

import uuid
from datetime import UTC, datetime

import pytest

from tests.conftest import TENANT_HEADERS

NOW = datetime.now(tz=UTC)


def _plantilla_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "nombre": "Oficio estándar",
        "tipo_documental": "Oficio",
        "cuerpo": "Cordial saludo, ...",
        "activo": True,
        "created_at": NOW,
    }
    base.update(kw)
    return base


def _borrador_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "doc_type": "S",
        "subject": "Respuesta a solicitud",
        "dest_dept": "Jurídica",
        "cuerpo": "Texto",
        "plantilla_id": None,
        "estado": "borrador",
        "radicado_id": None,
        "creado_por": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_crear_plantilla(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = _plantilla_row()
    resp = await client.post(
        "/api/v1/plantillas",
        json={
            "nombre": "Oficio estándar",
            "cuerpo": "Cordial saludo, ...",
            "tipo_documental": "Oficio",
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    assert resp.json()["nombre"] == "Oficio estándar"


@pytest.mark.asyncio
async def test_crear_borrador_desde_plantilla(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    pid = uuid.uuid4()
    # get_plantilla (cuerpo) → insert_borrador
    mock_conn.fetchrow.side_effect = [
        _plantilla_row(id=pid, cuerpo="Cuerpo de plantilla"),
        _borrador_row(plantilla_id=pid, cuerpo="Cuerpo de plantilla"),
    ]
    resp = await client.post(
        "/api/v1/borradores",
        json={"doc_type": "S", "subject": "Resp.", "plantilla_id": str(pid)},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    assert resp.json()["estado"] == "borrador"


@pytest.mark.asyncio
async def test_editar_borrador_radicado_409(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = _borrador_row(estado="radicado")
    resp = await client.patch(
        f"/api/v1/borradores/{uuid.uuid4()}",
        json={"subject": "Nuevo"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "borrador_radicado"


@pytest.mark.asyncio
async def test_aprobar_borrador(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    bid = uuid.uuid4()
    # _require_editable (get) → set_estado (RETURNING aprobado)
    mock_conn.fetchrow.side_effect = [
        _borrador_row(id=bid, estado="borrador"),
        _borrador_row(id=bid, estado="aprobado"),
    ]
    resp = await client.post(f"/api/v1/borradores/{bid}/aprobar", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["estado"] == "aprobado"
