# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests unitarios de `POST /api/v1/knowledge/antecedentes` (E21 Inc.2, Patrón A).

Cliente + conexión mockeada (``client_with_conn``, ver ``tests/conftest.py``) —
mismo patrón que ``test_knowledge.py`` para ``/search``. NUNCA se llama al modelo
real de embeddings (``EMBEDDING_PROVIDER=stub`` forzado a nivel de módulo por el
conftest padre).

Ninguno de estos tests manda ``X-User-Id``: el ``caller_id`` resuelve a ``None``
y ``resolve_caller_clearance`` devuelve PUBLICA(1) SIN tocar la conexión (ver su
docstring) — así el mock de ``conn.fetchrow`` queda libre para representar
EXCLUSIVAMENTE la lectura de ``get_pivot_chunk`` (la única llamada a
``fetchrow`` que hace el endpoint en la rama ``radicado_ref``), sin tener que
orquestar un ``side_effect`` con dos llamadas distintas.

Las invariantes que requieren Postgres real (pre-filtro ACL end-to-end, la
exclusión del pivote contra datos reales, el colapso 404 anti-oráculo con
clearance real, y el aislamiento por tenant) viven en
``tests/integration/test_antecedentes_acl_pivot.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.routers.knowledge import ANTECEDENTES_ADVISORY
from tests.conftest import TENANT_HEADERS

NOW = datetime.now(tz=UTC)

URL = "/api/v1/knowledge/antecedentes"


def _hit(source_ref: str, *, texto: str = "texto", score: float = 0.5) -> dict:
    return {
        "id": uuid.uuid4(),
        "source_type": "radicado",
        "source_ref": source_ref,
        "texto": texto,
        "metadata": {},
        "score": score,
    }


# --- Camino `query` -----------------------------------------------------------


@pytest.mark.asyncio
async def test_query_path_returns_anchored_hits_with_source_ref(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetch.return_value = [_hit("2024-DEMO-E-000001", texto="contrato de obra", score=0.91)]

    resp = await client.post(
        URL, json={"query": "contratos de obra pública", "top_k": 3}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["antecedentes"] == [
        {
            "source_type": "radicado",
            "source_ref": "2024-DEMO-E-000001",
            "snippet": "contrato de obra",
            "score": 0.91,
        }
    ]
    assert body["advisory"] == ANTECEDENTES_ADVISORY
    # Camino `query`: NO hay lectura de pivote (get_pivot_chunk usa fetchrow).
    assert conn.fetchrow.call_count == 0
    # `search` recibió max_clearance=1 (PUBLICA, sin X-User-Id) y top_k tal cual.
    call = conn.fetch.call_args.args
    assert call[2] == 1  # max_clearance
    assert call[3] == 3  # top_k (sin +1: no hay pivote que excluir)


@pytest.mark.asyncio
async def test_query_path_never_widens_acl_filter(client_with_conn) -> None:
    """El body de `/antecedentes` no admite `acl` (a diferencia de `/search`):
    el pre-filtro server-side (clearance) es el único acotador."""
    client, conn = client_with_conn
    conn.fetch.return_value = []

    resp = await client.post(URL, json={"query": "algo"}, headers=TENANT_HEADERS)

    assert resp.status_code == 200
    sql = conn.fetch.call_args.args[0]
    assert "acl @>" not in sql


# --- Camino `radicado_ref` (pivote) --------------------------------------------


@pytest.mark.asyncio
async def test_radicado_ref_path_uses_pivot_embedding_and_excludes_self(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = {"embedding_literal": "[0.1,0.2]", "nivel_seguridad": 1}
    conn.fetch.return_value = [
        _hit("2024-DEMO-E-000001", texto="el propio pivote", score=1.0),  # se excluye
        _hit("2024-DEMO-E-000002", texto="antecedente real", score=0.8),
    ]

    resp = await client.post(
        URL,
        json={"radicado_ref": "2024-DEMO-E-000001", "top_k": 5},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["antecedentes"]) == 1
    assert body["antecedentes"][0]["source_ref"] == "2024-DEMO-E-000002"
    assert body["advisory"] == ANTECEDENTES_ADVISORY

    # get_pivot_chunk: source_type default "radicado", source_ref = radicado_ref.
    pivot_call = conn.fetchrow.call_args.args
    assert pivot_call[1] == "radicado"
    assert pivot_call[2] == "2024-DEMO-E-000001"
    assert pivot_call[3] == 1, "el clearance del llamante viaja al WHERE del pivote ($3)"

    # search() recibió el embedding DEL PIVOTE (no uno recién embebido) y pidió
    # top_k + 1 (uno de más para poder excluir el pivote y aun así completar top_k).
    search_call = conn.fetch.call_args.args
    assert search_call[1] == "[0.1,0.2]"
    assert search_call[2] == 1  # max_clearance = PUBLICA (pivote nivel 1 <= 1)
    assert search_call[3] == 6  # top_k(5) + 1


@pytest.mark.asyncio
async def test_radicado_ref_path_respects_top_k_after_exclusion(client_with_conn) -> None:
    """Si `search` devuelve más candidatos de los pedidos (incl. el pivote), la
    respuesta final queda recortada a `top_k`, no a `top_k + 1`."""
    client, conn = client_with_conn
    conn.fetchrow.return_value = {"embedding_literal": "[0.1,0.2]", "nivel_seguridad": 1}
    conn.fetch.return_value = [
        _hit("PIVOT", score=1.0),
        *[_hit(f"OTRO-{i}", score=0.9 - i * 0.01) for i in range(7)],
    ]

    resp = await client.post(
        URL, json={"radicado_ref": "PIVOT", "top_k": 3}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["antecedentes"]) == 3
    refs = [a["source_ref"] for a in body["antecedentes"]]
    assert "PIVOT" not in refs


@pytest.mark.asyncio
async def test_radicado_ref_path_uses_custom_source_type(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = {"embedding_literal": "[0.3,0.4]", "nivel_seguridad": 1}
    conn.fetch.return_value = []

    resp = await client.post(
        URL,
        json={"radicado_ref": "EXP-1", "source_type": "expediente", "top_k": 5},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200
    pivot_call = conn.fetchrow.call_args.args
    assert pivot_call[1] == "expediente"
    assert pivot_call[2] == "EXP-1"


# --- XOR: exactamente uno de query/radicado_ref --------------------------------


@pytest.mark.asyncio
async def test_xor_both_fields_is_422(client_with_conn) -> None:
    client, _conn = client_with_conn
    resp = await client.post(
        URL, json={"query": "algo", "radicado_ref": "2024-DEMO-E-000001"}, headers=TENANT_HEADERS
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_xor_neither_field_is_422(client_with_conn) -> None:
    client, _conn = client_with_conn
    resp = await client.post(URL, json={}, headers=TENANT_HEADERS)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_xor_only_query_is_ok(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetch.return_value = []
    resp = await client.post(URL, json={"query": "solo query"}, headers=TENANT_HEADERS)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_xor_only_radicado_ref_is_ok(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = {"embedding_literal": "[0.1]", "nivel_seguridad": 1}
    conn.fetch.return_value = []
    resp = await client.post(URL, json={"radicado_ref": "solo-ref"}, headers=TENANT_HEADERS)
    assert resp.status_code == 200


# --- 404 anti-oráculo: inexistente == sobre-clearance (CRÍTICO) ----------------


@pytest.mark.asyncio
async def test_radicado_ref_not_indexed_is_404(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.return_value = None  # get_pivot_chunk: nada indexado

    resp = await client.post(
        URL, json={"radicado_ref": "no-existe"}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "antecedente_source_not_found"
    # El pivote no se resolvió: search() NUNCA se invoca.
    assert conn.fetch.call_count == 0


@pytest.mark.asyncio
async def test_radicado_ref_over_clearance_is_the_same_404(client_with_conn) -> None:
    """CRÍTICO: un radicado_ref que SÍ existe pero cuyo nivel_seguridad excede el
    clearance del llamante (PUBLICA=1 por defecto, sin X-User-Id) da el MISMO 404
    que uno inexistente — sin 403 diferenciado (anti-oráculo)."""
    client, conn = client_with_conn

    conn.fetchrow.return_value = None  # get_pivot_chunk: nada indexado
    resp_missing = await client.post(
        URL, json={"radicado_ref": "no-existe"}, headers=TENANT_HEADERS
    )

    # El gate vive en el WHERE: la base no devuelve fila para un pivote sobre el clearance.
    conn.fetchrow.return_value = None
    resp_over_clearance = await client.post(
        URL, json={"radicado_ref": "clasificado-existente"}, headers=TENANT_HEADERS
    )

    assert resp_missing.status_code == resp_over_clearance.status_code == 404
    assert resp_missing.json() == resp_over_clearance.json()
    assert resp_over_clearance.json()["detail"]["error"] == "antecedente_source_not_found"
    # El pivote sobre-clearance NUNCA se filtra hacia `search` como vector query.
    assert conn.fetch.call_count == 0
