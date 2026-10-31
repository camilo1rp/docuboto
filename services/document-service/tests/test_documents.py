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
from datetime import UTC, datetime
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


def _clearance_row(max_level: int = 3, is_root: bool = False) -> dict:
    """Fila que consume `resolve_caller_clearance` (SELECT is_root, max_level).

    `TENANT_HEADERS` incluye `X-User-Id`, así que todo endpoint con gate de
    clearance consume UN `fetchrow` antes de la consulta de negocio.
    """
    return {"is_root": is_root, "max_level": max_level}


_CREATE_PAYLOAD = {
    "doc_type": "E",
    "subject": "Solicitud de información",
    "doc_class": "Derechos de Petición",
    "dest_dept": "Dirección General",
    "dest_dept_code": 1,
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
    # clearance del llamante; get_disposition (existe, {}) → {"disposition": None};
    # set_disposition → {"disposition": <json>}
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        {"disposition": None},
        {"disposition": '{"accion": "eliminar", "marcado_eliminacion": true}'},
    ]
    resp = await client.patch(
        f"/api/v1/documents/{doc_id}/disposition",
        json={
            "accion": "eliminar",
            "marcado_eliminacion": True,
            # Obligatorio desde 11.10: eliminar es irreversible y se motiva.
            "fundamento": "Serie 200-15 con disposición de eliminación en la TRD",
        },
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
        _clearance_row(),  # resolve_caller_clearance
        {"disposition": None},  # get_disposition (existe)
        {"disposition": '{"accion": "eliminar", "marcado_eliminacion": true}'},  # set result
    ]
    response = await client.patch(
        f"/api/v1/documents/{doc_id}/disposition",
        json={
            "accion": "eliminar",
            "marcado_eliminacion": True,
            "fundamento": "Serie 200-15 con disposición de eliminación en la TRD",
        },
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
# No-read-up (RF-SEG-08) en disposición y PATCH.
#
# El gate viaja en el `WHERE` de la consulta, así que con `AsyncMock` no se
# "ejecuta" el filtro: se verifican las DOS mitades por separado —
#   (a) la consulta enviada lleva `nivel_seguridad <= $N` con el clearance real;
#   (b) cuando la fila no aparece, la respuesta es un 404 INDISTINGUIBLE del de
#       un radicado inexistente (propiedad de no-oráculo).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_disposition_consulta_lleva_gate_de_clearance(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """El SELECT de disposición filtra por el clearance resuelto del llamante."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [_clearance_row(max_level=2), {"disposition": None}]

    resp = await client.get(
        f"/api/v1/documents/{uuid.uuid4()}/disposition",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 200

    sql, *params = mock_conn.fetchrow.call_args[0]
    assert "nivel_seguridad <= $2" in sql
    assert params[1] == 2, "debe viajar el clearance REAL, no un valor abierto"


@pytest.mark.asyncio
async def test_set_disposition_consulta_lleva_gate_y_no_escribe_sobre_clearance(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Un radicado por encima del clearance no se lee NI se muta: 404 y cero UPDATE.

    Es la mitad que más importa: la disposición decide conservación total frente
    a eliminación (Ley 594/2000 art. 24).
    """
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    # clearance 1; el SELECT gated no encuentra fila (el radicado es RESERVADO).
    mock_conn.fetchrow.side_effect = [_clearance_row(max_level=1), None]

    resp = await client.patch(
        f"/api/v1/documents/{uuid.uuid4()}/disposition",
        json={
            "accion": "eliminar",
            "marcado_eliminacion": True,
            "fundamento": "Serie 200-15 con disposición de eliminación en la TRD",
        },
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 404
    consultas = [c[0][0] for c in mock_conn.fetchrow.call_args_list]
    assert not any("UPDATE radicados" in q for q in consultas), (
        "la escritura no debe intentarse siquiera cuando la lectura gated falla"
    )


@pytest.mark.asyncio
async def test_disposition_sobre_clearance_es_indistinguible_de_inexistente(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """No-oráculo: 404 idéntico para «no existe» y «existe pero te supera».

    Si difirieran (p. ej. 403 frente a 404), un usuario sin nivel podría sondear
    el servicio para confirmar la EXISTENCIA de radicados reservados.
    """
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    doc_id = uuid.uuid4()

    # (1) Radicado inexistente: clearance alto, la fila no está.
    mock_conn.fetchrow.side_effect = [_clearance_row(max_level=3), None]
    inexistente = await client.get(
        f"/api/v1/documents/{doc_id}/disposition",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    # (2) Radicado RESERVADO y llamante sin nivel: el gate lo excluye igual.
    mock_conn.fetchrow.side_effect = [_clearance_row(max_level=1), None]
    sobre_clearance = await client.get(
        f"/api/v1/documents/{doc_id}/disposition",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert inexistente.status_code == sobre_clearance.status_code == 404
    assert inexistente.json() == sobre_clearance.json()


@pytest.mark.asyncio
async def test_update_document_no_read_up_404(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """PATCH sobre un radicado que supera el clearance: 404 neutro.

    El PATCH devuelve el `DocumentResponse` COMPLETO, así que sin gate era además
    una lectura total (asunto, remitente, anexos) del radicado clasificado.
    """
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [_clearance_row(max_level=1), None]

    resp = await client.patch(
        f"/api/v1/documents/{uuid.uuid4()}",
        json={"subject": "Intento de lectura por escritura"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "document_not_found"

    # El clearance se aplica ya en la lectura previa (la que alimenta el diff del
    # asiento), así que aquí ni se llega al UPDATE: el radicado por encima del
    # clearance sale por el mismo 404 neutro. Que el gate viaje ADEMÁS en el WHERE
    # del propio UPDATE —que es lo que cierra la ventana TOCTOU— se comprueba en
    # `test_update_document_gate_y_guard_van_en_el_where`, donde el UPDATE sí corre.
    sql = mock_conn.fetchrow.call_args[0][0]
    assert "nivel_seguridad <=" in sql, "la lectura previa debe llevar el mismo gate"


# ---------------------------------------------------------------------------
# PATCH /api/v1/documents/{document_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_document(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """PATCH updates subject, returns updated radicado."""
    client, mock_conn = client_with_conn
    doc_id = uuid.uuid4()
    updated = make_radicado_record(
        doc_id=doc_id,
        subject="Asunto actualizado",
    )

    # update: fetchrow → updated radicado, fetch → [] (no anexos)
    mock_conn.fetchrow.return_value = updated
    mock_conn.fetch.return_value = []

    response = await client.patch(
        f"/api/v1/documents/{doc_id}",
        json={"subject": "Asunto actualizado"},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["subject"] == "Asunto actualizado"


@pytest.mark.asyncio
async def test_update_encola_document_radicado_updated(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Un cambio de datos deja `document.radicado.updated` en la misma transacción."""
    client, mock_conn = client_with_conn
    doc_id = uuid.uuid4()
    antes = make_radicado_record(doc_id=doc_id, subject="Asunto anterior")
    despues = make_radicado_record(doc_id=doc_id, subject="Asunto actualizado")

    async def fetchrow(sql: object, *_args: object, **_kwargs: object) -> dict:
        texto = str(sql)
        if "UPDATE radicados" in texto:
            return despues
        if "FROM radicados" in texto:
            return antes
        return despues

    mock_conn.fetchrow.side_effect = fetchrow
    mock_conn.fetch.return_value = []

    response = await client.patch(
        f"/api/v1/documents/{doc_id}",
        json={"subject": "Asunto actualizado"},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200
    assert any(
        "document.radicado.updated" in str(call.args)
        for call in mock_conn.execute.await_args_list
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("valor", ["distributed", "closed", "in_progress", "registered", None])
async def test_patch_con_status_es_422_status_managed_by_workflow(
    client_with_conn: tuple[AsyncClient, AsyncMock], valor: str | None
) -> None:
    """ADR-028: el estado del radicado lo mueve el flujo. Un `status` en el PATCH se
    RECHAZA con 422 explícito (no se ignora en silencio: el cliente creería que
    cambió) y no se toca la base."""
    client, mock_conn = client_with_conn
    doc_id = uuid.uuid4()

    response = await client.patch(
        f"/api/v1/documents/{doc_id}",
        json={"subject": "Asunto", "status": valor},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "status_managed_by_workflow"
    assert not any(
        "UPDATE radicados" in str(c.args[0]) for c in mock_conn.fetchrow.await_args_list
    )


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
    # No-write-up: radicar en nivel 2 resuelve primero el clearance del actor
    # (fetchrow, is_root → CLASIFICADA=3 ≥ 2), luego template → tenant_code → create.
    mock_conn.fetchrow.side_effect = [{"is_root": True}, None, {"code": "DEMO"}, record]
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = []

    response = await client.post(
        "/api/v1/documents",
        json={
            **_CREATE_PAYLOAD,
            "nivel_seguridad": 2,
            "clasificacion_fundamento": "Art. 19 Ley 1712",
            "clasificacion_causal": "art19_b_seguridad_publica",
            "clasificacion_plazo_meses": 12,
        },
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    assert response.json()["nivel_seguridad"] == 2


@pytest.mark.asyncio
async def test_create_document_no_write_up_403(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """No-write-up: un PUBLICA no puede radicar en nivel 3 → 403 (RF-SEG-08)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # permiso PERM_RADI concedido
    mock_conn.fetchrow.return_value = None  # resolve_caller_clearance → PUBLICA (1)
    response = await client.post(
        "/api/v1/documents",
        json={
            **_CREATE_PAYLOAD,
            "nivel_seguridad": 3,
            "clasificacion_fundamento": "Art. 18 Ley 1712",
            "clasificacion_causal": "art18_a_intimidad",
        },
        headers=TENANT_HEADERS,
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "clasificacion_forbidden"


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
    """`/health` es VIVACIDAD: gobierna los reinicios del contenedor.

    Antes tomaba una conexión del pool en cada sondeo (cada 15 s) para preguntar
    `SELECT 1`, metiendo una dependencia externa en la sonda que decide si el
    contenedor se reinicia. Reiniciar por un Postgres caído no arregla Postgres.
    El diagnóstico de dependencias vive ahora en `/health/ready`.
    """
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "document-service"}


# ── 11.10: la disposición decide destruir, y se trataba como una lectura ──────


@pytest.mark.asyncio
async def test_fijar_disposicion_exige_nivel_de_escritura(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """El defecto: `require_permission("USUA_PERM_EXPEDIENTE")` con el default
    `min_crud=1`. Cualquiera que pudiera LEER la disposición podía marcar el
    radicado para eliminación — mismo permiso, mismo nivel, acto opuesto.

    Se comprueba sobre la consulta RBAC que sale de verdad (`gp.crud >= $3`),
    no sobre la firma: el nivel exigido es un ARGUMENTO, y un test que solo
    mirara la lista de dependencias seguiría verde con el valor equivocado.
    """
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        {"disposition": None},
        {"disposition": '{"accion": "conservar"}'},
    ]

    await client.patch(
        f"/api/v1/documents/{uuid.uuid4()}/disposition",
        json={"accion": "conservar"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    rbac = [c for c in mock_conn.fetchval.call_args_list if "auth_users" in str(c[0][0])]
    assert rbac, "no se consultó el RBAC"
    # (sql, user_id, perm_name, min_crud)
    assert rbac[0][0][2] == "USUA_PERM_EXPEDIENTE"
    assert rbac[0][0][3] == 3, (
        "el PATCH de disposición debe exigir escritura (min_crud=3), como las "
        "disposiciones de storage-service"
    )


@pytest.mark.asyncio
async def test_eliminar_sin_fundamento_se_rechaza(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Ley 594/2000 art. 24: la eliminación es irreversible y debe motivarse."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    response = await client.patch(
        f"/api/v1/documents/{uuid.uuid4()}/disposition",
        json={"accion": "eliminar", "marcado_eliminacion": True},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert response.status_code == 422
    assert "fundamento" in response.text


@pytest.mark.asyncio
async def test_conservar_no_exige_fundamento(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """El defecto simétrico: exigirlo en el camino que no destruye nada es
    fricción que no protege y que acaba rellenándose con cualquier palabra,
    devaluando el campo donde sí importa."""
    client, mock_conn = client_with_conn
    doc_id = uuid.uuid4()
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        {"disposition": None},
        {"disposition": '{"accion": "conservar"}'},
    ]

    response = await client.patch(
        f"/api/v1/documents/{doc_id}/disposition",
        json={"accion": "conservar"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_fijar_disposicion_deja_asiento_con_el_estado_anterior(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """No dejaba rastro de quién decidió eliminar.

    El estado anterior va en el asiento: sin él, la cadena dice que alguien fijó
    «eliminar» pero no qué había antes, que es la mitad de la pregunta cuando se
    audita una eliminación años después.
    """
    client, mock_conn = client_with_conn
    doc_id = uuid.uuid4()
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        {"disposition": '{"accion": "conservar"}'},
        {"disposition": '{"accion": "eliminar"}'},
    ]

    response = await client.patch(
        f"/api/v1/documents/{doc_id}/disposition",
        json={"accion": "eliminar", "fundamento": "TRD serie 200-15"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert response.status_code == 200
    inserciones = [str(c[0][0]) for c in mock_conn.execute.call_args_list]
    assert any("audit_log" in q for q in inserciones), "el acto no dejó asiento"
    argumentos = " ".join(str(c) for c in mock_conn.execute.call_args_list)
    assert "disposition_set" in argumentos
    assert "conservar" in argumentos, "el estado anterior no llegó al asiento"


# ── 11.1: esquema de publicación (Ley 1712/2014 art. 12) ─────────────────────


@pytest.mark.asyncio
async def test_el_recorte_publico_exige_el_acto_de_publicacion(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """El defecto: «público» se DEDUCÍA de la ausencia de reserva.

    Un radicado sin clasificar se cosechaba solo por OAI-PMH. Este test mira el
    chokepoint, que es el único sitio donde el recorte se decide: si alguien lo
    relaja, aquí se ve.
    """
    from app.repositories.oai_repository import _PUBLIC

    assert "publicable" in _PUBLIC, "el recorte público no exige el acto de publicación"
    # Las tres condiciones se exigen a la vez: la publicación no puede sacar a
    # la luz un documento reservado ni uno anulado.
    assert "nivel_seguridad = 1" in _PUBLIC
    assert "anulado" in _PUBLIC


@pytest.mark.asyncio
async def test_publicar_deja_asiento_y_devuelve_el_estado(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    doc_id = uuid.uuid4()
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        {
            "tracking_number": "2026-DEMO-E-000042",
            "publicable": True,
            "publicado_at": datetime(2026, 8, 11, tzinfo=UTC),
        },
    ]

    resp = await client.post(
        f"/api/v1/documents/{doc_id}/publicacion",
        json={"publicar": True, "fundamento": "Esquema de publicación 2026, numeral 4.2"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 200
    assert resp.json()["publicable"] is True
    ejecutadas = " ".join(str(c) for c in mock_conn.execute.call_args_list)
    assert "publicacion_activada" in ejecutadas, "el acto no dejó asiento"
    # Sin la tilde: `json.dumps` la escapa a \u00f3 en el payload del asiento.
    assert "numeral 4.2" in ejecutadas


@pytest.mark.asyncio
async def test_publicar_sin_fundamento_se_rechaza(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Y también RETIRAR sin fundamento: un campo obligatorio en un solo sentido
    invita a usar el otro para deshacer sin dar cuentas."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    for cuerpo in ({"publicar": True}, {"publicar": False}):
        resp = await client.post(
            f"/api/v1/documents/{uuid.uuid4()}/publicacion",
            json=cuerpo,
            headers={**TENANT_HEADERS, "X-User-Id": _USER},
        )
        assert resp.status_code == 422, cuerpo


@pytest.mark.asyncio
async def test_publicar_un_reservado_da_404_neutro(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """El UPDATE exige `nivel_seguridad = 1` en su propio WHERE.

    Guardar «esto es público» sobre un documento reservado no es algo que se
    filtre después: la próxima desclasificación lo publicaría sin que nadie lo
    hubiera decidido. Y el 404 es el mismo que para «no existe», para no
    convertir el endpoint en un oráculo.
    """
    client, mock_conn = client_with_conn

    # El gate RBAC concede; la comprobación diagnóstica de C-4/C10 («¿lo único que falla
    # es el tipo?») responde que NO: el radicado es reservado, así que sigue el 404 neutro.
    async def _fetchval(sql, *args, **kw):
        return False if "FROM radicados" in sql else True

    mock_conn.fetchval.side_effect = _fetchval
    mock_conn.fetchrow.side_effect = [_clearance_row(), None]

    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/publicacion",
        json={"publicar": True, "fundamento": "x"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "document_not_found"
    consultas = [str(c[0][0]) for c in mock_conn.fetchrow.call_args_list]
    update = next(q for q in consultas if "UPDATE radicados" in q and "publicable" in q)
    assert "nivel_seguridad = 1" in update


@pytest.mark.asyncio
async def test_publicar_exige_la_competencia_sobre_el_regimen_de_acceso(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Mismo permiso que decide la RESERVA, con nivel de escritura: repartirlos
    permitiría que quien no puede desclasificar publicara igualmente."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        {"tracking_number": "T", "publicable": True, "publicado_at": None},
    ]

    await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/publicacion",
        json={"publicar": True, "fundamento": "x"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    rbac = [c for c in mock_conn.fetchval.call_args_list if "auth_users" in str(c[0][0])]
    assert rbac[0][0][2] == "PERM_RECLASIFICAR"
    assert rbac[0][0][3] == 3


# ---------------------------------------------------------------------------
# Lectura del estado de publicación (Ley 1712/2014 art. 12)
#
# El acto de publicación se podía EJECUTAR pero su resultado no era legible por
# ninguna vía: `DocumentResponse` no exponía `publicable`, así que ni la
# interfaz podía ofrecer el acto sobre un estado conocido ni la entidad
# acreditar qué había publicado. Un esquema de publicación que no se puede
# consultar no es un esquema, es una escritura ciega.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_el_detalle_expone_el_estado_de_publicacion(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    doc_id = uuid.uuid4()
    publicado = datetime(2026, 8, 12, 10, 30, tzinfo=UTC)
    mock_conn.fetchrow.return_value = make_radicado_record(
        doc_id=doc_id, publicable=True, publicado_at=publicado
    )
    mock_conn.fetch.return_value = []

    resp = await client.get(f"/api/v1/documents/{doc_id}", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert body["publicable"] is True
    assert body["publicado_at"] is not None


@pytest.mark.asyncio
async def test_el_detalle_pide_las_columnas_de_publicacion(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Contrato del SQL: con mocks, el `SELECT` es lo único observable.

    Sin esta comprobación el test anterior seguiría verde tras quitar las
    columnas de la consulta — el valor lo pondría el mock, no la base.
    """
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = make_radicado_record()
    mock_conn.fetch.return_value = []

    await client.get(f"/api/v1/documents/{uuid.uuid4()}", headers=TENANT_HEADERS)

    consulta = next(
        str(c[0][0]) for c in mock_conn.fetchrow.call_args_list if "FROM radicados" in str(c[0][0])
    )
    assert "publicable" in consulta
    assert "publicado_at" in consulta


@pytest.mark.asyncio
async def test_el_listado_expone_el_estado_de_publicacion(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = [make_radicado_record(publicable=True)]

    resp = await client.get("/api/v1/documents?page=1&size=20", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    assert resp.json()[0]["publicable"] is True
    consulta = next(
        str(c[0][0]) for c in mock_conn.fetch.call_args_list if "FROM radicados" in str(c[0][0])
    )
    assert "publicable" in consulta


@pytest.mark.asyncio
async def test_una_fila_sin_la_columna_se_lee_como_no_publicada(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """La polaridad del default importa, y por eso se fija en un test.

    El mapeo tolera que una consulta futura no traiga las columnas. El único
    default admisible es «no publicado»: al revés, una consulta incompleta
    haría *parecer* publicado material que la entidad nunca decidió difundir.
    """
    client, mock_conn = client_with_conn
    fila = make_radicado_record()
    del fila["publicable"]
    del fila["publicado_at"]
    mock_conn.fetchrow.return_value = fila
    mock_conn.fetch.return_value = []

    resp = await client.get(f"/api/v1/documents/{uuid.uuid4()}", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    assert resp.json()["publicable"] is False


# ---------------------------------------------------------------------------
# Auditoría de conformidad (2026-08-13): tres canales, un solo acto
#
# El acto de publicación se aplicó al recorte de metadatos y NO a la copia
# escrita a mano que sirve los BYTES por CMIS, ni a la interacción con la
# reserva. Los dos fallos son de alcance, no de diseño.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_el_contenido_servido_por_cmis_exige_el_acto_de_publicacion() -> None:
    """Servir los bytes es la difusión más intensa: no puede tener otra polaridad.

    Se inspecciona el SQL **que la consulta envía**, no el código fuente del
    método: la primera versión de este test buscaba `_public(` en
    `inspect.getsource(...)` y lo encontraba en el docstring, así que sobrevivía
    a la mutación. Un test que se conforma con lo que el código *dice* hacer
    reproduce exactamente el defecto que persigue — aquí el `WHERE` llevaba una
    copia obsoleta mientras el docstring afirmaba anclar en el chokepoint.
    """
    from app.repositories.oai_repository import OaiRepository

    conn = AsyncMock()
    conn.fetchrow.return_value = None
    await OaiRepository(conn).get_public_anexo("2026-DEMO-E-000001")

    sql = str(conn.fetchrow.call_args[0][0])
    assert "r.publicable" in sql, "el contenido se sirve sin exigir el acto de publicación"
    assert "r.nivel_seguridad = 1" in sql
    assert "r.status <> 'anulado'" in sql


@pytest.mark.asyncio
async def test_retirar_la_publicacion_no_exige_nivel_publico(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Un radicado publicado y clasificado después no podía despublicarse: el
    estado en el que más urge retirar era el único sin salida."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        {"tracking_number": "T", "publicable": False, "publicado_at": None},
    ]

    await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/publicacion",
        json={"publicar": False, "fundamento": "Retiro por reclasificación sobrevenida"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    consultas = [str(c[0][0]) for c in mock_conn.fetchrow.call_args_list]
    update = next(q for q in consultas if "UPDATE radicados" in q and "publicable" in q)
    assert "nivel_seguridad <= $3" in update, "el clearance sigue siendo obligatorio"
    assert "nivel_seguridad = 1" not in update, "el retiro no debe exigir nivel público"


@pytest.mark.asyncio
async def test_publicar_si_exige_nivel_publico(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """La asimetría es el punto: relajar el retiro no debe relajar la publicación."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        {"tracking_number": "T", "publicable": True, "publicado_at": None},
    ]

    await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/publicacion",
        json={"publicar": True, "fundamento": "Numeral 4.2 del esquema"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    consultas = [str(c[0][0]) for c in mock_conn.fetchrow.call_args_list]
    update = next(q for q in consultas if "UPDATE radicados" in q and "publicable" in q)
    assert "nivel_seguridad = 1" in update
