# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de búsqueda avanzada (E09, ADR-014)."""

import uuid
from datetime import UTC, datetime

import pytest

from tests.conftest import TENANT_HEADERS


def _hit_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "tracking_number": "2024-DEMO-E-000001",
        "doc_type": "E",
        "subject": "Acción de tutela",
        "sender_name": "Juan Pérez",
        "sender_entity": "Ciudadano",
        # La proyección real la trae siempre: quien vincula un radicado a un
        # expediente la necesita para aportar el <tipologia> del índice
        # electrónico, porque archive-service no lee esta tabla.
        "doc_class": "COR-PET",
        "dest_dept": "Jurídica",
        # La proyección real trae la ubicación ACTUAL (ADR-029), distinta de la
        # dependencia de radicación `dest_dept` cuando el flujo movió el radicado.
        "ubicacion_dept_code": 120,
        "ubicacion_dept": "Jurídica",
        "status": "registered",
        "registered_at": datetime.now(tz=UTC),
        "rank": 0.5,
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_search_with_text_and_filters(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = [_hit_row()]

    resp = await client.get(
        "/api/v1/search?q=tutela&doc_type=E&date_from=2024-01-01", headers=TENANT_HEADERS
    )
    assert resp.status_code == 200
    assert resp.headers["x-total-count"] == "1"
    body = resp.json()
    assert body["total"] == 1
    assert body["hits"][0]["doc_type"] == "E"
    # La consulta usó FTS (websearch_to_tsquery) en el WHERE.
    where_sql = mock_conn.fetchval.call_args[0][0]
    assert "websearch_to_tsquery" in where_sql


@pytest.mark.asyncio
async def test_search_filters_only_no_text(client_with_conn) -> None:
    """Sin q es una consulta filtrada (sin predicado de texto), ordenada por fecha."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 2
    mock_conn.fetch.return_value = [_hit_row(rank=0.0), _hit_row(rank=0.0)]

    resp = await client.get(
        "/api/v1/search?status=registered&meta.area=juridica", headers=TENANT_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 2
    # Sin texto: no hay tsquery; sí hay filtro de metadato JSONB.
    rows_sql = mock_conn.fetch.call_args[0][0]
    assert "websearch_to_tsquery" not in rows_sql
    where_sql = mock_conn.fetchval.call_args[0][0]
    assert "metadata @>" in where_sql


@pytest.mark.asyncio
async def test_search_always_filters_by_clearance(client_with_conn) -> None:
    """RF-SEG-08 / RF-BUS-04: el WHERE siempre acota por nivel de seguridad y el
    primer parámetro es el clearance del llamante (fail-closed a PUBLICA=1 cuando
    el tenant no tiene tablas RBAC en este test de unidad)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None  # usuario sin clearance → PUBLICA
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = [_hit_row()]

    resp = await client.get("/api/v1/search?q=tutela", headers=TENANT_HEADERS)
    assert resp.status_code == 200

    count_call = mock_conn.fetchval.call_args[0]
    where_sql = count_call[0]
    assert "nivel_seguridad <=" in where_sql
    # El clearance es el primer parámetro vinculado (condición añadida primero).
    assert count_call[1] == 1
    # También en la consulta de filas.
    assert "nivel_seguridad <=" in mock_conn.fetch.call_args[0][0]


@pytest.mark.asyncio
async def test_search_empty(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 0
    mock_conn.fetch.return_value = []

    resp = await client.get("/api/v1/search?q=inexistente", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == {"query": "inexistente", "total": 0, "hits": [], "page": 1, "size": 20}


# ---------------------------------------------------------------------------
# Acentos (ADR-014) — revisión por navegador, B9
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_la_busqueda_ignora_los_acentos(client_with_conn) -> None:
    """Consulta e índice tienen que usar LA MISMA configuración de texto.

    Buscar «certificacion» devolvía cero radicados mientras «certificación»
    devolvía tres: el `search_vector` se construía con `spanish` y la consulta
    también, y esa configuración distingue acentos. En un sistema que usan
    funcionarios escribiendo deprisa eso no es un caso exótico, y falla en
    silencio — «Sin resultados» es indistinguible de «no existe».

    Se comprueba sobre el SQL ENVIADO: que el resultado venga de una fila
    simulada no diría nada sobre qué configuración pide la consulta.
    """
    client, mock_conn = client_with_conn
    consultas: list[str] = []

    async def fake_fetch(query, *params):
        consultas.append(query)
        return []

    mock_conn.fetch.side_effect = fake_fetch
    mock_conn.fetchval.return_value = 0

    resp = await client.get("/api/v1/search?q=certificacion", headers=TENANT_HEADERS)
    assert resp.status_code == 200

    sql = next(q for q in consultas if "search_vector" in q)
    assert "'public.spanish_unaccent'::regconfig" in sql
    # Gemelo: dejar `'spanish'` en una de las dos llamadas —el filtro o el
    # ranking— pasaría desapercibido y ordenaría por una relevancia calculada
    # con otro diccionario.
    assert "websearch_to_tsquery('spanish'" not in sql
