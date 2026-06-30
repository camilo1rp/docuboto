# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests for document-service endpoints.

The database connection is fully mocked — no PostgreSQL instance required.
Each test configures mock_conn return values to match the exact call sequence
used by the repository layer.

Repository call sequence for CREATE:
  1. execute  — SET search_path (get_tenant_code)
  2. fetchrow — SELECT code FROM public.tenants
  3. execute  — SET search_path (next_sequence)
  4. fetchval — INSERT ON CONFLICT RETURNING last_seq
  5. execute  — SET search_path (create radicado)
  6. fetchrow — INSERT INTO radicados RETURNING ...
  7. fetch    — SELECT anexos (empty when no anexos in payload)

Repository call sequence for GET by id / tracking:
  1. execute  — SET search_path
  2. fetchrow — SELECT radicado
  3. fetch    — SELECT anexos

Repository call sequence for LIST:
  1. execute  — SET search_path
  2. fetchval — COUNT(*)
  3. fetch    — SELECT radicados

Repository call sequence for UPDATE:
  1. execute  — SET search_path
  2. fetchrow — UPDATE … RETURNING
  3. fetch    — SELECT anexos
"""

import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from app.core.database import get_tenant_conn
from app.main import app
from tests.conftest import (
    TENANT_HEADERS,
    make_anexo_record,
    make_radicado_record,
)

_CREATE_PAYLOAD = {
    "doc_type": "E",
    "subject": "Solicitud de información",
    "doc_class": "Derechos de Petición",
    "dest_dept": "Dirección General",
    "sender_name": "Juan Pérez",
    "sender_entity": "Ciudadano",
    "pages": 3,
}


# ---------------------------------------------------------------------------
# POST /api/v1/documents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_document_entrada(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """POST creates a radicado type E and returns 201 with formatted tracking_number."""
    client, mock_conn = client_with_conn
    doc_id = uuid.uuid4()
    record = make_radicado_record(doc_id=doc_id)

    # get_tenant_code: fetchrow returns tenant row
    # next_sequence:   fetchval returns sequence 1
    # create:          fetchrow returns radicado row
    # _get_anexos:     fetch returns [] (no anexos in payload)
    mock_conn.fetchrow.side_effect = [None, {"code": "DEMO"}, record]
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = []

    response = await client.post(
        "/api/v1/documents",
        json=_CREATE_PAYLOAD,
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert "tracking_number" in body
    # Tracking number must include year, tenant code, type, and zero-padded sequence
    tn = body["tracking_number"]
    parts = tn.split("-")
    assert len(parts) == 4
    assert parts[1] == "DEMO"
    assert parts[2] == "E"
    assert len(parts[3]) == 6  # zero-padded to 6 digits
    assert body["doc_type"] == "E"
    assert body["status"] == "registered"
    assert body["id"] == str(doc_id)


@pytest.mark.asyncio
async def test_create_document_without_response_days_has_no_due_date(
    client_with_deps,
) -> None:
    """Sin response_days no se llama a tenant-service y due_date queda None."""
    client, mock_conn, fake_client = client_with_deps
    record = make_radicado_record(due_date=None)
    mock_conn.fetchrow.side_effect = [None, {"code": "DEMO"}, record]
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = []

    response = await client.post("/api/v1/documents", json=_CREATE_PAYLOAD, headers=TENANT_HEADERS)

    assert response.status_code == 201
    assert response.json()["due_date"] is None
    assert fake_client.calls == []  # no se consultó el calendario


@pytest.mark.asyncio
async def test_create_document_with_response_days_sets_due_date(
    client_with_deps,
) -> None:
    """Con response_days se calcula due_date vía tenant-service (RF-RAD-04)."""
    client, mock_conn, fake_client = client_with_deps
    from datetime import date

    fake_client.due_date = date(2024, 1, 22)
    record = make_radicado_record(due_date=date(2024, 1, 22))
    mock_conn.fetchrow.side_effect = [None, {"code": "DEMO"}, record]
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = []

    response = await client.post(
        "/api/v1/documents",
        json={**_CREATE_PAYLOAD, "response_days": 15},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    assert response.json()["due_date"] == "2024-01-22"
    # Se reenvió el tenant y los 15 días hábiles solicitados.
    assert len(fake_client.calls) == 1
    slug, _from, days = fake_client.calls[0]
    assert slug == "demo"
    assert days == 15


@pytest.mark.asyncio
async def test_create_document_due_date_failure_is_502(
    client_with_deps,
) -> None:
    """Si tenant-service falla, la creación devuelve 502 (no se radica a medias)."""
    client, mock_conn, fake_client = client_with_deps
    fake_client.raise_error = True

    response = await client.post(
        "/api/v1/documents",
        json={**_CREATE_PAYLOAD, "response_days": 15},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 502
    assert response.json()["detail"]["error"] == "tenant_service_unavailable"


@pytest.mark.asyncio
async def test_create_document_with_valid_dest_dependencia(
    client_with_deps,
) -> None:
    """dest_dept_code válido: se valida vía HTTP y se denormaliza el nombre."""
    client, mock_conn, fake_client = client_with_deps
    fake_client.dependencias[120] = {
        "codigo": 120,
        "sigla": "RYC",
        "nombre": "Registro y Control",
        "activo": True,
    }
    # El radicado persistido refleja el nombre denormalizado y el código.
    record = make_radicado_record(dest_dept="Registro y Control")
    record["dest_dept_code"] = 120
    mock_conn.fetchrow.side_effect = [None, {"code": "DEMO"}, record]
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = []

    response = await client.post(
        "/api/v1/documents",
        json={**_CREATE_PAYLOAD, "dest_dept_code": 120},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["dest_dept_code"] == 120
    assert body["dest_dept"] == "Registro y Control"
    assert fake_client.dep_calls == [("demo", 120)]


@pytest.mark.asyncio
async def test_create_document_unknown_dependencia_is_400(
    client_with_deps,
) -> None:
    """dest_dept_code inexistente → 400 invalid_dependencia (no se radica)."""
    client, mock_conn, fake_client = client_with_deps
    # fake_client.dependencias vacío → get_dependencia devuelve None.

    response = await client.post(
        "/api/v1/documents",
        json={**_CREATE_PAYLOAD, "dest_dept_code": 999},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_dependencia"


@pytest.mark.asyncio
async def test_create_document_inactive_dependencia_is_400(
    client_with_deps,
) -> None:
    """Dependencia inactiva → 400 invalid_dependencia."""
    client, mock_conn, fake_client = client_with_deps
    fake_client.dependencias[55] = {
        "codigo": 55,
        "sigla": "OLD",
        "nombre": "Dependencia Suprimida",
        "activo": False,
    }

    response = await client.post(
        "/api/v1/documents",
        json={**_CREATE_PAYLOAD, "dest_dept_code": 55},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_dependencia"


@pytest.mark.asyncio
async def test_create_document_emits_event(client_with_redis) -> None:
    """Al radicar se publica un evento en orpycamcp.document.events (ADR-010)."""
    client, mock_conn, fake_redis = client_with_redis
    record = make_radicado_record(tracking_number="2024-DEMO-E-000001")
    mock_conn.fetchrow.side_effect = [None, {"code": "DEMO"}, record]
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = []

    response = await client.post("/api/v1/documents", json=_CREATE_PAYLOAD, headers=TENANT_HEADERS)

    assert response.status_code == 201
    # Se hizo XADD al stream del dominio con el sobre canónico.
    assert fake_redis.xadd.await_count == 1
    stream, fields = fake_redis.xadd.await_args.args
    assert stream == "orpycamcp.document.events"
    assert fields["event_type"] == "document.radicado.created"
    assert fields["tenant_slug"] == "demo"


@pytest.mark.asyncio
async def test_create_document_survives_event_failure(client_with_redis) -> None:
    """Si Redis falla, la radicación NO se revierte (evento best-effort)."""
    client, mock_conn, fake_redis = client_with_redis
    fake_redis.xadd.side_effect = RuntimeError("redis down")
    record = make_radicado_record()
    mock_conn.fetchrow.side_effect = [None, {"code": "DEMO"}, record]
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = []

    response = await client.post("/api/v1/documents", json=_CREATE_PAYLOAD, headers=TENANT_HEADERS)

    assert response.status_code == 201  # el radicado se creó pese al fallo de Redis


@pytest.mark.asyncio
async def test_create_document_sin_permiso_403(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """D-12: PERM_RADI requerido para radicar; sin permiso → 403 (sin asignar tracking_number)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None  # check_permission → False → 403
    response = await client.post(
        "/api/v1/documents",
        json=_CREATE_PAYLOAD,
        headers=TENANT_HEADERS,
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_create_document_missing_tenant_header(
    client: AsyncClient,
) -> None:
    """POST without X-Tenant-Slug returns 400 (get_tenant_conn validation)."""
    # Quita el override para que la dependencia real valide el header.
    app.dependency_overrides.pop(get_tenant_conn, None)
    response = await client.post("/api/v1/documents", json=_CREATE_PAYLOAD)
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_tenant"


@pytest.mark.asyncio
async def test_create_document_invalid_type(
    client: AsyncClient,
) -> None:
    """POST with invalid doc_type returns 422."""
    payload = {**_CREATE_PAYLOAD, "doc_type": "X"}
    response = await client.post(
        "/api/v1/documents",
        json=payload,
        headers=TENANT_HEADERS,
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/documents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_documents(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """GET / returns array and X-Total-Count header."""
    client, mock_conn = client_with_conn
    records = [
        make_radicado_record(tracking_number="2024-DEMO-E-000001"),
        make_radicado_record(tracking_number="2024-DEMO-S-000001", doc_type="S"),
    ]
    mock_conn.fetchval.return_value = 2
    mock_conn.fetch.return_value = records

    response = await client.get("/api/v1/documents", headers=TENANT_HEADERS)

    assert response.status_code == 200
    assert response.headers["x-total-count"] == "2"
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 2


@pytest.mark.asyncio
async def test_list_documents_filter_by_type(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """GET /?doc_type=E filters and returns only Entrada documents."""
    client, mock_conn = client_with_conn
    records = [make_radicado_record(doc_type="E")]
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = records

    response = await client.get("/api/v1/documents?doc_type=E", headers=TENANT_HEADERS)

    assert response.status_code == 200
    assert response.headers["x-total-count"] == "1"
    body = response.json()
    assert len(body) == 1
    assert body[0]["doc_type"] == "E"


# ---------------------------------------------------------------------------
# GET /api/v1/documents/{document_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_documents_metadata_filter(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """GET /?meta.area=juridica filtra por campo de metadato (índice GIN)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = [make_radicado_record(metadata={"area": "juridica"})]

    response = await client.get("/api/v1/documents?meta.area=juridica", headers=TENANT_HEADERS)
    assert response.status_code == 200
    assert response.headers["x-total-count"] == "1"
    # El COUNT recibió el filtro JSONB (@>).
    count_sql = mock_conn.fetchval.call_args[0][0]
    assert "metadata @>" in count_sql


@pytest.mark.asyncio
async def test_set_and_get_disposition(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """PATCH y GET /{id}/disposition fijan y leen los metadatos de disposición."""
    client, mock_conn = client_with_conn
    doc_id = uuid.uuid4()
    # set: get_disposition (existe, {}) → fetchrow {"disposition": None};
    #      set_disposition → fetchrow {"disposition": <json>}
    mock_conn.fetchrow.side_effect = [
        {"disposition": None},
        {"disposition": '{"accion": "eliminar", "marcado_eliminacion": true}'},
    ]
    resp = await client.patch(
        f"/api/v1/documents/{doc_id}/disposition",
        json={"accion": "eliminar", "marcado_eliminacion": True},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["disposition"]["accion"] == "eliminar"
    assert body["disposition"]["marcado_eliminacion"] is True


@pytest.mark.asyncio
async def test_disposition_not_found(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None  # radicado inexistente
    resp = await client.get(f"/api/v1/documents/{uuid.uuid4()}/disposition", headers=TENANT_HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_document_by_id(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """GET /{id} returns 200 with radicado including anexos list."""
    client, mock_conn = client_with_conn
    doc_id = uuid.uuid4()
    record = make_radicado_record(doc_id=doc_id)
    anexo = make_anexo_record(radicado_id=doc_id)

    # get_by_id: fetchrow → radicado, fetch → [anexo]
    mock_conn.fetchrow.return_value = record
    mock_conn.fetch.return_value = [anexo]

    response = await client.get(f"/api/v1/documents/{doc_id}", headers=TENANT_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(doc_id)
    assert isinstance(body["anexos"], list)
    assert len(body["anexos"]) == 1
    assert body["anexos"][0]["filename"] == "carta.pdf"


@pytest.mark.asyncio
async def test_get_document_by_id_is_clearance_scoped(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """RF-SEG-08: el GET de detalle acota por clasificación — la consulta filtra
    `nivel_seguridad <= clearance` (un doc por encima devuelve None → 404)."""
    client, mock_conn = client_with_conn
    doc_id = uuid.uuid4()
    mock_conn.fetchrow.return_value = make_radicado_record(doc_id=doc_id)
    mock_conn.fetch.return_value = []

    resp = await client.get(f"/api/v1/documents/{doc_id}", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    # La última consulta (get_by_id) incluye el predicado de clearance.
    assert "nivel_seguridad <=" in mock_conn.fetchrow.call_args.args[0]


@pytest.mark.asyncio
async def test_get_document_not_found(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """GET /{id} returns 404 when document does not exist."""
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None

    response = await client.get(f"/api/v1/documents/{uuid.uuid4()}", headers=TENANT_HEADERS)

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "document_not_found"


# ---------------------------------------------------------------------------
# GET /api/v1/documents/by-tracking/{tracking_number}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_document_by_tracking(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """GET /by-tracking/{tn} returns the radicado matching the tracking number."""
    client, mock_conn = client_with_conn
    doc_id = uuid.uuid4()
    tracking = "2024-DEMO-E-000001"
    record = make_radicado_record(doc_id=doc_id, tracking_number=tracking)

    mock_conn.fetchrow.return_value = record
    mock_conn.fetch.return_value = []

    response = await client.get(f"/api/v1/documents/by-tracking/{tracking}", headers=TENANT_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["tracking_number"] == tracking
    assert body["id"] == str(doc_id)


# ---------------------------------------------------------------------------
# D-13: comprobaciones de autorización (PERM_RADI / USUA_PERM_EXPEDIENTE)
# ---------------------------------------------------------------------------

_USER = str(uuid.uuid4())


@pytest.mark.asyncio
async def test_update_document_sin_permiso_403(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """D-13: PERM_RADI requerido para actualizar radicado; sin permiso → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    response = await client.patch(
        f"/api/v1/documents/{uuid.uuid4()}",
        json={"subject": "Intento sin permiso"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_update_document_con_permiso_200(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """PERM_RADI concedido → actualizar radicado devuelve 200."""
    client, mock_conn = client_with_conn
    doc_id = uuid.uuid4()
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = make_radicado_record(doc_id=doc_id, subject="Actualizado")
    mock_conn.fetch.return_value = []
    response = await client.patch(
        f"/api/v1/documents/{doc_id}",
        json={"subject": "Actualizado"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert response.status_code == 200
    assert response.json()["subject"] == "Actualizado"


@pytest.mark.asyncio
async def test_set_disposition_sin_permiso_403(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """D-13: USUA_PERM_EXPEDIENTE requerido para fijar disposición; sin permiso → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    response = await client.patch(
        f"/api/v1/documents/{uuid.uuid4()}/disposition",
        json={"accion": "eliminar", "marcado_eliminacion": True},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_set_disposition_con_permiso_200(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """USUA_PERM_EXPEDIENTE concedido → fijar disposición devuelve 200."""
    client, mock_conn = client_with_conn
    doc_id = uuid.uuid4()
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        {"disposition": None},  # get_disposition (existe)
        {"disposition": '{"accion": "eliminar", "marcado_eliminacion": true}'},  # set result
    ]
    response = await client.patch(
        f"/api/v1/documents/{doc_id}/disposition",
        json={"accion": "eliminar", "marcado_eliminacion": True},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert response.status_code == 200
    assert response.json()["disposition"]["accion"] == "eliminar"


@pytest.mark.asyncio
async def test_get_disposition_sin_permiso_403(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """D-13: USUA_PERM_EXPEDIENTE requerido para leer disposición; sin permiso → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    response = await client.get(
        f"/api/v1/documents/{uuid.uuid4()}/disposition",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_get_disposition_con_permiso_200(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """USUA_PERM_EXPEDIENTE concedido → leer disposición devuelve 200."""
    client, mock_conn = client_with_conn
    doc_id = uuid.uuid4()
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = {"disposition": None}
    response = await client.get(
        f"/api/v1/documents/{doc_id}/disposition",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert response.status_code == 200
    assert response.json()["radicado_id"] == str(doc_id)


# ---------------------------------------------------------------------------
# PATCH /api/v1/documents/{document_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_document(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """PATCH updates subject and status, returns updated radicado."""
    client, mock_conn = client_with_conn
    doc_id = uuid.uuid4()
    updated = make_radicado_record(
        doc_id=doc_id,
        subject="Asunto actualizado",
        status="distributed",
    )

    # update: fetchrow → updated radicado, fetch → [] (no anexos)
    mock_conn.fetchrow.return_value = updated
    mock_conn.fetch.return_value = []

    response = await client.patch(
        f"/api/v1/documents/{doc_id}",
        json={"subject": "Asunto actualizado", "status": "distributed"},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["subject"] == "Asunto actualizado"
    assert body["status"] == "distributed"


@pytest.mark.asyncio
async def test_update_immutable_tracking_number(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """PATCH with tracking_number in body is silently ignored — schema has no such field."""
    client, mock_conn = client_with_conn
    doc_id = uuid.uuid4()
    original = make_radicado_record(doc_id=doc_id, tracking_number="2024-DEMO-E-000001")

    mock_conn.fetchrow.return_value = original
    mock_conn.fetch.return_value = []

    response = await client.patch(
        f"/api/v1/documents/{doc_id}",
        # DocumentUpdate schema has no tracking_number field; Pydantic ignores it
        json={"tracking_number": "2024-DEMO-E-999999", "subject": "New subject"},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200
    # tracking_number must remain unchanged
    assert response.json()["tracking_number"] == "2024-DEMO-E-000001"


# ---------------------------------------------------------------------------
# nivel_seguridad exposed in all read paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_document_returns_nivel_seguridad(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """POST /documents returns nivel_seguridad in the 201 response body."""
    client, mock_conn = client_with_conn
    record = make_radicado_record(nivel_seguridad=2)
    mock_conn.fetchrow.side_effect = [None, {"code": "DEMO"}, record]
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = []

    response = await client.post(
        "/api/v1/documents",
        json={**_CREATE_PAYLOAD, "nivel_seguridad": 2},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    assert response.json()["nivel_seguridad"] == 2


@pytest.mark.asyncio
async def test_get_document_by_id_returns_nivel_seguridad(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """GET /{id} includes nivel_seguridad in the response body."""
    client, mock_conn = client_with_conn
    doc_id = uuid.uuid4()
    record = make_radicado_record(doc_id=doc_id, nivel_seguridad=3)
    mock_conn.fetchrow.return_value = record
    mock_conn.fetch.return_value = []

    response = await client.get(f"/api/v1/documents/{doc_id}", headers=TENANT_HEADERS)

    assert response.status_code == 200
    assert response.json()["nivel_seguridad"] == 3


@pytest.mark.asyncio
async def test_get_document_by_tracking_returns_nivel_seguridad(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """GET /by-tracking/{tn} includes nivel_seguridad in the response body."""
    client, mock_conn = client_with_conn
    tracking = "2024-DEMO-I-000001"
    record = make_radicado_record(
        tracking_number=tracking, doc_type="I", nivel_seguridad=2
    )
    mock_conn.fetchrow.return_value = record
    mock_conn.fetch.return_value = []

    response = await client.get(
        f"/api/v1/documents/by-tracking/{tracking}", headers=TENANT_HEADERS
    )

    assert response.status_code == 200
    assert response.json()["nivel_seguridad"] == 2


@pytest.mark.asyncio
async def test_list_documents_returns_nivel_seguridad(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """GET / returns nivel_seguridad on each item in the list."""
    client, mock_conn = client_with_conn
    records = [
        make_radicado_record(tracking_number="2024-DEMO-E-000001", nivel_seguridad=1),
        make_radicado_record(
            tracking_number="2024-DEMO-E-000002", doc_type="S", nivel_seguridad=2
        ),
    ]
    mock_conn.fetchval.return_value = 2
    mock_conn.fetch.return_value = records

    response = await client.get("/api/v1/documents", headers=TENANT_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body[0]["nivel_seguridad"] == 1
    assert body[1]["nivel_seguridad"] == 2


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient) -> None:
    """Health endpoint returns 200 with structured status."""
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["database"] == "ok"
    assert body["status"] == "ok"
