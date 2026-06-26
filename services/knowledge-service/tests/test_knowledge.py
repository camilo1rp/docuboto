# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de la capa de conocimiento (E21): embeddings stub, ingesta, búsqueda."""

import uuid
from datetime import UTC, datetime

import pytest

from app.services.embeddings import embed, to_pgvector
from tests.conftest import TENANT_HEADERS, TEST_INTERNAL_TOKEN

NOW = datetime.now(tz=UTC)


def test_embed_deterministic_and_dim() -> None:
    a = embed("acción de tutela", dim=64)
    b = embed("acción de tutela", dim=64)
    assert a == b  # determinista
    assert len(a) == 64  # dimensión fija
    assert embed("otro texto", dim=64) != a
    # normalizado L2 (norma ~1) salvo texto vacío
    import math

    assert abs(math.sqrt(sum(x * x for x in a)) - 1.0) < 1e-6


def test_to_pgvector_format() -> None:
    lit = to_pgvector([0.5, -0.25])
    assert lit.startswith("[") and lit.endswith("]")
    assert "0.500000" in lit


@pytest.mark.asyncio
async def test_ingest(client_with_conn, token_settings) -> None:
    """Ingesta autorizada (token de servicio interno correcto) → 201."""
    client, conn = client_with_conn
    cid = uuid.uuid4()
    conn.fetchrow.return_value = {
        "id": cid,
        "source_type": "radicado",
        "source_ref": "2024-DEMO-E-000001",
        "created_at": NOW,
    }
    resp = await client.post(
        "/api/v1/knowledge/ingest",
        json={
            "source_type": "radicado",
            "source_ref": "2024-DEMO-E-000001",
            "texto": "Solicitud de información sobre contrato",
            "nivel_seguridad": 1,
            "acl": {"dependencias": [120]},
        },
        headers={**TENANT_HEADERS, "X-Internal-Token": TEST_INTERNAL_TOKEN},
    )
    assert resp.status_code == 201
    assert resp.json()["source_ref"] == "2024-DEMO-E-000001"
    # El INSERT recibió un literal vector y el ACL.
    sql = conn.fetchrow.call_args.args[0]
    assert "::vector" in sql and "knowledge_chunk" in sql


@pytest.mark.asyncio
async def test_ingest_without_token_is_rejected(client_with_conn, token_settings) -> None:
    """D-02: ingesta sin X-Internal-Token es rechazada con 401 (header ausente)."""
    client, _conn = client_with_conn
    resp = await client.post(
        "/api/v1/knowledge/ingest",
        json={
            "source_type": "radicado",
            "source_ref": "2024-DEMO-E-000001",
            "texto": "Solicitud de información sobre contrato",
            "nivel_seguridad": 1,
        },
        headers=TENANT_HEADERS,  # sin X-Internal-Token
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "missing_internal_token"


@pytest.mark.asyncio
async def test_ingest_with_wrong_token_is_rejected(client_with_conn, token_settings) -> None:
    """D-02: ingesta con token incorrecto es rechazada con 403."""
    client, _conn = client_with_conn
    resp = await client.post(
        "/api/v1/knowledge/ingest",
        json={
            "source_type": "radicado",
            "source_ref": "2024-DEMO-E-000001",
            "texto": "Solicitud de información sobre contrato",
            "nivel_seguridad": 1,
        },
        headers={**TENANT_HEADERS, "X-Internal-Token": "wrong-token"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "invalid_internal_token"


@pytest.mark.asyncio
async def test_search_with_acl(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetch.return_value = [
        {
            "id": uuid.uuid4(),
            "source_type": "radicado",
            "source_ref": "R-1",
            "texto": "contrato de obra",
            "metadata": {},
            "score": 0.87,
        },
    ]
    resp = await client.post(
        "/api/v1/knowledge/search",
        json={"query": "contratos", "top_k": 3, "acl": {"dependencias": [120]}},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["score"] == 0.87
    # El pre-filtrado ACL entró en el WHERE.
    sql = conn.fetch.call_args.args[0]
    assert "acl @>" in sql and "embedding <=>" in sql


@pytest.mark.asyncio
async def test_search_always_enforces_clearance_server_side(client_with_conn) -> None:
    """RF-SEG-08 / RF-BUS-04: aunque el cliente NO mande `acl`, la búsqueda acota por
    la clasificación del chunk ≤ clearance del llamante (PUBLICA=1 sin X-User-Id)."""
    client, conn = client_with_conn
    conn.fetch.return_value = []
    resp = await client.post(
        "/api/v1/knowledge/search", json={"query": "algo"}, headers=TENANT_HEADERS
    )
    assert resp.status_code == 200
    call = conn.fetch.call_args.args
    sql = call[0]
    assert "nivel_seguridad" in sql  # filtro de clearance server-side
    assert call[2] == 1  # max_clearance = PUBLICA (sin identidad)


@pytest.mark.asyncio
async def test_search_empty(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetch.return_value = []
    resp = await client.post(
        "/api/v1/knowledge/search", json={"query": "nada"}, headers=TENANT_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json() == []
