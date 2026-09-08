# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Auditoría best-effort de LECTURAS de radicados (E01/E09, RF-BUS-10; Ley
1712/2014, Decreto 1081/2015).

Hasta este incremento, document-service auditaba escrituras
(``document.radicado_created``, reclasificación) pero ninguna lectura —
consultar un radicado RESERVADO/CLASIFICADO no dejaba rastro. Cubre:

  (a) lectura individual exitosa (GET /{id} y GET /by-tracking/{tn}) por
      api/ui/mcp con actor real -> UNA entrada ``document.radicado_consultado``
      con ``object_ref=tracking_number``.
  (b) sin actor real (sin X-User-Id), o canal no auditable ('system', por
      ausencia de X-Canal) -> NO se audita.
  (c) listado/búsqueda exitosos -> UNA sola entrada agregada
      ``document.radicado_busqueda``, NUNCA una por fila/hit.
  (d) el payload de búsqueda nunca contiene el texto crudo de ``q`` (solo
      ``q_presente``/``q_len``).
  (e) best-effort real: si ``audit.append`` explota, la lectura sigue
      devolviendo 200 con los datos (nunca degrada a 500).

Espejo de ``test_rbac_denial_audit.py`` (mismo patrón de inspección del SQL
crudo ejecutado contra la conexión mockeada, sin mockear ``audit.append``
salvo en los casos de fallo best-effort).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from tests.conftest import TENANT_HEADERS, TENANT_SLUG, make_radicado_record


def _audit_insert_calls(mock_conn: AsyncMock) -> list:
    return [
        c
        for c in mock_conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]


def _hit_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "tracking_number": "2024-DEMO-E-000001",
        "doc_type": "E",
        "subject": "Acción de tutela",
        "sender_name": "Juan Pérez",
        "sender_entity": "Ciudadano",
        "doc_class": "COR-PET",
        "dest_dept": "Jurídica",
        "status": "registered",
        "registered_at": datetime.now(tz=UTC),
        "rank": 0.5,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# (a) Lectura individual — GET /{document_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("canal", ["api", "ui", "mcp"])
async def test_get_document_by_id_audits_individual_read(
    client_with_conn: tuple[AsyncClient, AsyncMock], canal: str
) -> None:
    client, mock_conn = client_with_conn
    doc_id = uuid.uuid4()
    tracking = "2024-DEMO-E-000001"
    mock_conn.fetchrow.return_value = make_radicado_record(
        doc_id=doc_id, tracking_number=tracking
    )
    mock_conn.fetch.return_value = []

    resp = await client.get(
        f"/api/v1/documents/{doc_id}",
        headers={**TENANT_HEADERS, "X-Canal": canal},
    )

    assert resp.status_code == 200
    inserts = _audit_insert_calls(mock_conn)
    assert len(inserts) == 1
    args = inserts[0].args
    # _INSERT_SQL params: (sql, tenant, service, actor, canal, action,
    #                       object_type, object_ref, payload_json, prev_hash, hash)
    assert args[1] == TENANT_HEADERS["X-Tenant-Slug"]
    assert args[2] == "document-service"
    assert args[3] == TENANT_HEADERS["X-User-Id"]
    assert args[4] == canal
    assert args[5] == "document.radicado_consultado"
    assert args[6] == "radicado"
    assert args[7] == tracking


# ---------------------------------------------------------------------------
# (a) Lectura individual — GET /by-tracking/{tracking_number}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_document_by_tracking_audits_individual_read(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    tracking = "2024-DEMO-I-000007"
    mock_conn.fetchrow.return_value = make_radicado_record(
        tracking_number=tracking, doc_type="I"
    )
    mock_conn.fetch.return_value = []

    resp = await client.get(
        f"/api/v1/documents/by-tracking/{tracking}",
        headers={**TENANT_HEADERS, "X-Canal": "ui"},
    )

    assert resp.status_code == 200
    inserts = _audit_insert_calls(mock_conn)
    assert len(inserts) == 1
    args = inserts[0].args
    assert args[4] == "ui"
    assert args[5] == "document.radicado_consultado"
    assert args[6] == "radicado"
    assert args[7] == tracking


# ---------------------------------------------------------------------------
# (b) Gating: sin actor real / canal no auditable -> NO se audita
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_document_no_audit_without_user_id(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Sin X-User-Id (llamada anónima o de servicio): sigue siendo 200, pero
    sin actor real no hay a quién atribuir la lectura."""
    client, mock_conn = client_with_conn
    doc_id = uuid.uuid4()
    mock_conn.fetchrow.return_value = make_radicado_record(doc_id=doc_id)
    mock_conn.fetch.return_value = []

    resp = await client.get(
        f"/api/v1/documents/{doc_id}",
        headers={"X-Tenant-Slug": TENANT_SLUG, "X-Canal": "api"},
    )

    assert resp.status_code == 200
    assert _audit_insert_calls(mock_conn) == []


@pytest.mark.asyncio
async def test_get_document_no_audit_when_canal_absent_defaults_system(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Sin X-Canal (el gateway no lo inyectó / llamada interna), el canal por
    defecto es 'system' -> fuera de _CANALES_AUDITABLES -> NO se audita, pese
    a que TENANT_HEADERS sí trae un X-User-Id real."""
    client, mock_conn = client_with_conn
    doc_id = uuid.uuid4()
    mock_conn.fetchrow.return_value = make_radicado_record(doc_id=doc_id)
    mock_conn.fetch.return_value = []

    resp = await client.get(f"/api/v1/documents/{doc_id}", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    assert _audit_insert_calls(mock_conn) == []


@pytest.mark.asyncio
async def test_get_document_by_tracking_no_audit_without_user_id(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    tracking = "2024-DEMO-E-000009"
    mock_conn.fetchrow.return_value = make_radicado_record(tracking_number=tracking)
    mock_conn.fetch.return_value = []

    resp = await client.get(
        f"/api/v1/documents/by-tracking/{tracking}",
        headers={"X-Tenant-Slug": TENANT_SLUG, "X-Canal": "mcp"},
    )

    assert resp.status_code == 200
    assert _audit_insert_calls(mock_conn) == []


# ---------------------------------------------------------------------------
# (c) Traza agregada — GET /documents (listado)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_documents_audits_once_not_per_row(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    records = [
        make_radicado_record(tracking_number="2024-DEMO-E-000001"),
        make_radicado_record(tracking_number="2024-DEMO-E-000002", doc_type="S"),
        make_radicado_record(tracking_number="2024-DEMO-E-000003", doc_type="I"),
    ]
    mock_conn.fetchval.return_value = 3
    mock_conn.fetch.return_value = records

    resp = await client.get(
        "/api/v1/documents?doc_type=E",
        headers={**TENANT_HEADERS, "X-Canal": "ui"},
    )

    assert resp.status_code == 200
    assert len(resp.json()) == 3

    inserts = _audit_insert_calls(mock_conn)
    assert len(inserts) == 1  # UNA sola entrada, jamás una por fila
    args = inserts[0].args
    assert args[4] == "ui"
    assert args[5] == "document.radicado_busqueda"
    assert args[6] == "radicado_busqueda"
    assert args[7] == "listado"
    payload = json.loads(args[8])
    assert payload["total"] == 3
    assert payload["doc_type"] == "E"


@pytest.mark.asyncio
async def test_list_documents_no_audit_without_user_id(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = [make_radicado_record()]

    resp = await client.get(
        "/api/v1/documents",
        headers={"X-Tenant-Slug": TENANT_SLUG, "X-Canal": "api"},
    )

    assert resp.status_code == 200
    assert _audit_insert_calls(mock_conn) == []


# ---------------------------------------------------------------------------
# (c)+(d) Traza agregada — GET /search (búsqueda), sin texto crudo de q
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_documents_audits_once_not_per_hit(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 2
    mock_conn.fetch.return_value = [_hit_row(), _hit_row(tracking_number="2024-DEMO-E-000002")]

    resp = await client.get(
        "/api/v1/search?q=tutela&doc_type=E",
        headers={**TENANT_HEADERS, "X-Canal": "mcp"},
    )

    assert resp.status_code == 200
    assert resp.json()["total"] == 2

    inserts = _audit_insert_calls(mock_conn)
    assert len(inserts) == 1  # UNA sola entrada, jamás una por hit
    args = inserts[0].args
    assert args[4] == "mcp"
    assert args[5] == "document.radicado_busqueda"
    assert args[6] == "radicado_busqueda"
    assert args[7] == "listado"


@pytest.mark.asyncio
async def test_search_documents_payload_never_contains_raw_query_text(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """CRÍTICO de privacidad: el payload de auditoría lleva q_presente/q_len,
    JAMÁS el texto crudo de `q` (evita filtrar PII a `audit_log`, inmutable y
    de retención indefinida)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = [_hit_row()]

    secret_query = "diagnostico medico reservado del peticionario"
    resp = await client.get(
        "/api/v1/search",
        params={"q": secret_query},
        headers={**TENANT_HEADERS, "X-Canal": "api"},
    )

    assert resp.status_code == 200

    inserts = _audit_insert_calls(mock_conn)
    assert len(inserts) == 1
    payload_raw = inserts[0].args[8]
    assert secret_query not in payload_raw

    payload = json.loads(payload_raw)
    assert "q" not in payload
    assert payload["q_presente"] is True
    assert payload["q_len"] == len(secret_query)


@pytest.mark.asyncio
async def test_search_documents_no_audit_without_user_id(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = [_hit_row()]

    resp = await client.get(
        "/api/v1/search?q=tutela",
        headers={"X-Tenant-Slug": TENANT_SLUG, "X-Canal": "api"},
    )

    assert resp.status_code == 200
    assert _audit_insert_calls(mock_conn) == []


# ---------------------------------------------------------------------------
# (e) Best-effort real: un fallo de auditoría nunca degrada la lectura a 500
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_document_read_audit_best_effort_survives_failure(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    doc_id = uuid.uuid4()
    mock_conn.fetchrow.return_value = make_radicado_record(doc_id=doc_id)
    mock_conn.fetch.return_value = []

    with patch(
        "app.services.document_service.audit.append",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        resp = await client.get(
            f"/api/v1/documents/{doc_id}",
            headers={**TENANT_HEADERS, "X-Canal": "api"},
        )

    assert resp.status_code == 200
    assert resp.json()["id"] == str(doc_id)


@pytest.mark.asyncio
async def test_list_documents_search_audit_best_effort_survives_failure(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = [make_radicado_record()]

    with patch(
        "app.services.document_service.audit.append",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        resp = await client.get(
            "/api/v1/documents",
            headers={**TENANT_HEADERS, "X-Canal": "ui"},
        )

    assert resp.status_code == 200
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_search_documents_audit_best_effort_survives_failure(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = [_hit_row()]

    with patch(
        "app.services.document_service.audit.append",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        resp = await client.get(
            "/api/v1/search?q=tutela",
            headers={**TENANT_HEADERS, "X-Canal": "mcp"},
        )

    assert resp.status_code == 200
    assert resp.json()["total"] == 1


# ---------------------------------------------------------------------------
# (f) Salidas vinculadas — GET /{id}/respuestas: no-read-up + traza agregada
# ---------------------------------------------------------------------------


def _respuesta_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "tracking_number": "2024-DEMO-S-000009",
        "doc_type": "S",
        "subject": "Respuesta a la tutela",
        "status": "registered",
        "registered_at": datetime.now(tz=UTC),
    }
    base.update(kw)
    return base


def _respuestas_fetch_call(mock_conn: AsyncMock):
    """La llamada `fetch` del repo para las Salidas vinculadas (SQL `responde_a`)."""
    for c in mock_conn.fetch.call_args_list:
        if c.args and "responde_a" in c.args[0]:
            return c
    return None


@pytest.mark.asyncio
async def test_listar_respuestas_applies_no_read_up(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    # RF-SEG-08: el clearance del llamante (fetchval=1=PUBLICA en el mock) debe
    # llegar al SQL como el filtro `nivel_seguridad <= $2`.
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = [_respuesta_row()]

    doc_id = uuid.uuid4()
    resp = await client.get(
        f"/api/v1/documents/{doc_id}/respuestas",
        headers={**TENANT_HEADERS, "X-Canal": "api"},
    )

    assert resp.status_code == 200
    call = _respuestas_fetch_call(mock_conn)
    assert call is not None, "el repo debe consultar las Salidas vinculadas"
    assert "nivel_seguridad <= $2" in call.args[0]  # no-read-up en el SQL
    assert call.args[2] == 1  # el clearance resuelto se pasa como parámetro


@pytest.mark.asyncio
async def test_listar_respuestas_audits_aggregate_once(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = [_respuesta_row(), _respuesta_row()]

    doc_id = uuid.uuid4()
    resp = await client.get(
        f"/api/v1/documents/{doc_id}/respuestas",
        headers={**TENANT_HEADERS, "X-Canal": "mcp"},
    )

    assert resp.status_code == 200
    inserts = _audit_insert_calls(mock_conn)
    assert len(inserts) == 1  # UNA entrada agregada, jamás una por Salida
    args = inserts[0].args
    assert args[4] == "mcp"
    assert args[5] == "document.radicado_busqueda"
    assert args[6] == "radicado_busqueda"
    payload = json.loads(args[8])
    assert payload["antecedente_id"] == str(doc_id)
    assert payload["total"] == 2


@pytest.mark.asyncio
async def test_listar_respuestas_no_audit_without_user_id(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = [_respuesta_row()]

    headers = {k: v for k, v in TENANT_HEADERS.items() if k != "X-User-Id"}
    doc_id = uuid.uuid4()
    resp = await client.get(
        f"/api/v1/documents/{doc_id}/respuestas",
        headers={**headers, "X-Canal": "api"},
    )

    # sin actor real: se sirve la lectura (filtrada) pero NO se audita
    assert resp.status_code == 200
    assert _audit_insert_calls(mock_conn) == []


@pytest.mark.asyncio
async def test_listar_respuestas_audit_best_effort_survives_failure(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = [_respuesta_row()]

    with patch(
        "app.services.document_service.audit.append",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        doc_id = uuid.uuid4()
        resp = await client.get(
            f"/api/v1/documents/{doc_id}/respuestas",
            headers={**TENANT_HEADERS, "X-Canal": "api"},
        )

    assert resp.status_code == 200
    assert len(resp.json()) == 1
