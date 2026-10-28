# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La pasarela en modo `local` (ADR-046, fase D0): texto completo, sin generación.

document-service se simula con un ``httpx.MockTransport``: así se prueba también el
cliente (ruta, parámetros, identidad reenviada) y no solo el mapeo de la respuesta.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import httpx
import pytest

from app.clients import document_search
from app.routers.knowledge import ANTECEDENTES_ADVISORY
from tests.conftest import TENANT_HEADERS, USER_ID

pytestmark = pytest.mark.asyncio


def _hit(tracking: str, subject: str, *, rank: float = 0.5) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "tracking_number": tracking,
        "doc_type": "E",
        "subject": subject,
        "sender_name": "Ana",
        "sender_entity": None,
        "doc_class": None,
        "dest_dept": None,
        "status": "radicado",
        "registered_at": "2026-10-01T10:00:00Z",
        "rank": rank,
    }


@pytest.fixture
def document_service(monkeypatch: pytest.MonkeyPatch) -> Callable[..., list[httpx.Request]]:
    """Instala un document-service simulado; devuelve las peticiones que recibió."""
    recibidas: list[httpx.Request] = []

    def instalar(responder: Callable[[httpx.Request], httpx.Response]) -> list[httpx.Request]:
        def handler(req: httpx.Request) -> httpx.Response:
            recibidas.append(req)
            return responder(req)

        real = httpx.AsyncClient

        def cliente(*args, **kwargs):
            return real(*args, transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(document_search.httpx, "AsyncClient", cliente)
        return recibidas

    return instalar


def _por_q(respuestas: dict[str, list[dict]]) -> Callable[[httpx.Request], httpx.Response]:
    def responder(req: httpx.Request) -> httpx.Response:
        hits = respuestas.get(req.url.params["q"], [])
        return httpx.Response(200, json={"query": None, "total": len(hits), "hits": hits, "page": 1, "size": 20})

    return responder


# --- /search ------------------------------------------------------------------


async def test_search_consulta_document_service_como_el_usuario(client_with_conn, document_service) -> None:
    client, _ = client_with_conn
    recibidas = document_service(_por_q({"contrato obra": [_hit("2026-E-1", "Contrato de obra", rank=0.8)]}))

    resp = await client.post(
        "/api/v1/knowledge/search",
        json={"query": "contrato obra", "top_k": 7},
        headers={**TENANT_HEADERS, "X-Canal": "ui"},
    )

    assert resp.status_code == 200, resp.text
    [hit] = resp.json()
    assert hit["source_type"] == "radicado"
    assert hit["source_ref"] == "2026-E-1"
    assert hit["texto"] == "Contrato de obra"
    assert hit["score"] == 0.8
    assert hit["origin"] == "local_index"
    assert hit["metadata"]["doc_type"] == "E"

    [req] = recibidas
    assert req.method == "GET"
    assert req.url.path == "/api/v1/search"
    assert req.url.params["q"] == "contrato obra"
    assert req.url.params["size"] == "7"
    assert req.headers["X-User-Id"] == USER_ID
    assert req.headers["X-Tenant-Slug"] == "demo"
    assert req.headers["X-Canal"] == "ui"


@pytest.mark.parametrize("respuesta", [httpx.Response(500), httpx.Response(200, json={"x": 1})])
async def test_search_sin_document_service_es_503_no_una_lista_vacia(
    client_with_conn, document_service, respuesta
) -> None:
    client, _ = client_with_conn
    document_service(lambda _req: respuesta)

    resp = await client.post("/api/v1/knowledge/search", json={"query": "contrato"}, headers=TENANT_HEADERS)

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "search_unavailable"


async def test_search_inalcanzable_es_503(client_with_conn, document_service) -> None:
    client, _ = client_with_conn

    def caido(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin red")

    document_service(caido)
    resp = await client.post("/api/v1/knowledge/search", json={"query": "contrato"}, headers=TENANT_HEADERS)
    assert resp.status_code == 503


# --- /antecedentes ----------------------------------------------------------------


async def test_antecedentes_por_texto(client_with_conn, document_service) -> None:
    client, _ = client_with_conn
    document_service(_por_q({"licencia": [_hit("2026-E-2", "Licencia de construcción", rank=0.4)]}))

    resp = await client.post("/api/v1/knowledge/antecedentes", json={"query": "licencia"}, headers=TENANT_HEADERS)

    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["antecedentes"] == [
        {"source_type": "radicado", "source_ref": "2026-E-2", "snippet": "Licencia de construcción", "score": 0.4}
    ]
    assert cuerpo["advisory"] == ANTECEDENTES_ADVISORY
    assert cuerpo["origin"] == "local_index"


async def test_antecedentes_por_radicado_busca_por_su_asunto_y_lo_excluye(
    client_with_conn, document_service
) -> None:
    client, _ = client_with_conn
    pivote = _hit("2026-E-9", "Solicitud de licencia de construcción")
    otros = [_hit(f"2026-E-{i}", "Licencia de construcción", rank=0.5 - i / 100) for i in range(1, 4)]
    consulta = "solicitud or licencia or construcción"
    recibidas = document_service(_por_q({"2026-E-9": [pivote], consulta: [pivote, *otros]}))

    resp = await client.post(
        "/api/v1/knowledge/antecedentes", json={"radicado_ref": "2026-E-9", "top_k": 2}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 200, resp.text
    refs = [a["source_ref"] for a in resp.json()["antecedentes"]]
    assert refs == ["2026-E-1", "2026-E-2"]
    assert [r.url.params["q"] for r in recibidas] == ["2026-E-9", consulta]
    assert recibidas[1].url.params["size"] == "3"


async def test_antecedentes_de_un_radicado_invisible_es_404_neutro(client_with_conn, document_service) -> None:
    """document-service ya excluyó lo que excede el clearance: «no existe» y «no puede
    verlo» llegan igual, como lista sin el número exacto."""
    client, _ = client_with_conn
    document_service(_por_q({"2026-E-9": [_hit("2026-E-90", "Otro")]}))

    resp = await client.post(
        "/api/v1/knowledge/antecedentes", json={"radicado_ref": "2026-E-9"}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "antecedente_source_not_found"


async def test_antecedentes_de_otro_source_type_es_404(client_with_conn, document_service) -> None:
    client, _ = client_with_conn
    recibidas = document_service(_por_q({}))

    resp = await client.post(
        "/api/v1/knowledge/antecedentes",
        json={"radicado_ref": "EXP-1", "source_type": "expediente"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 404
    assert recibidas == []


@pytest.mark.parametrize("cuerpo", [{"query": "licencia", "radicado_ref": "R-1"}, {}])
async def test_antecedentes_exige_exactamente_uno(client_with_conn, cuerpo) -> None:
    client, _ = client_with_conn
    resp = await client.post("/api/v1/knowledge/antecedentes", json=cuerpo, headers=TENANT_HEADERS)
    assert resp.status_code == 422


# --- /rag y /availability -----------------------------------------------------------


async def test_rag_sin_proveedor_es_503_y_no_consulta_nada(client_with_conn, document_service) -> None:
    client, _ = client_with_conn
    recibidas = document_service(_por_q({}))

    resp = await client.post("/api/v1/knowledge/rag", json={"query": "plazo de respuesta"}, headers=TENANT_HEADERS)

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "rag_unavailable"
    assert recibidas == []


async def test_availability_dice_que_no_hay_generacion(client_with_conn) -> None:
    client, _ = client_with_conn
    resp = await client.get("/api/v1/knowledge/availability", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == {"generation": False, "mode": "local"}


async def test_las_rutas_retiradas_ya_no_existen(client_with_conn) -> None:
    client, _ = client_with_conn
    ingest = await client.post("/api/v1/knowledge/ingest", json={}, headers=TENANT_HEADERS)
    aceptar = await client.post(f"/api/v1/knowledge/suggestions/{uuid.uuid4()}/aceptar", headers=TENANT_HEADERS)
    assert ingest.status_code == 404
    assert aceptar.status_code == 404
