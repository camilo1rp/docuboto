# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de preservación digital: plan + eventos (E10)."""

import json
import uuid
from datetime import UTC, datetime

import pytest

from tests.conftest import TENANT_HEADERS

NOW = datetime.now(tz=UTC)


def _plan_row(version: int = 1) -> dict:
    return {
        "id": uuid.uuid4(),
        "version": version,
        "formatos_destino": ["PDF/A-2b", "TIFF"],
        "num_copias": 2,
        "periodicidad_fixity_dias": 180,
        "politica_migracion": "anual",
        "contingencia": None,
        "creado_por": "ana",
        "created_at": NOW,
    }


@pytest.mark.asyncio
async def test_set_plan_versions(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = 0  # MAX(version) → 0 → nueva = 1
    mock_conn.fetchrow.return_value = _plan_row(version=1)

    resp = await client.put(
        "/api/v1/preservacion/plan",
        json={
            "formatos_destino": ["PDF/A-2b", "TIFF"],
            "num_copias": 2,
            "periodicidad_fixity_dias": 180,
            "politica_migracion": "anual",
        },
        headers={**TENANT_HEADERS, "X-Username": "ana"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["version"] == 1
    assert body["num_copias"] == 2


@pytest.mark.asyncio
async def test_get_plan_not_found(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = None
    resp = await client.get("/api/v1/preservacion/plan", headers=TENANT_HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_record_evento(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    exp_ref = str(uuid.uuid4())
    h = "a" * 64
    mock_conn.fetchrow.return_value = {
        "id": uuid.uuid4(),
        "object_ref": exp_ref,
        "tipo": "FIJACION",
        "resultado": "ok",
        "hash_before": h,
        "hash_after": h,
        "detalle": json.dumps({"algoritmo": "SHA-256"}),
        "agente": "system",
        "ts": NOW,
    }
    resp = await client.post(
        "/api/v1/preservacion/eventos",
        json={
            "object_ref": exp_ref,
            "tipo": "FIJACION",
            "resultado": "ok",
            "hash_before": h,
            "hash_after": h,
            "detalle": {"algoritmo": "SHA-256"},
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["tipo"] == "FIJACION"
    assert body["detalle"]["algoritmo"] == "SHA-256"  # JSONB parseado


@pytest.mark.asyncio
async def test_list_eventos(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = [
        {
            "id": uuid.uuid4(),
            "object_ref": "exp-1",
            "tipo": "INGESTA",
            "resultado": "ok",
            "hash_before": None,
            "hash_after": None,
            "detalle": None,
            "agente": "system",
            "ts": NOW,
        },
    ]
    resp = await client.get("/api/v1/preservacion/eventos?object_ref=exp-1", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()[0]["tipo"] == "INGESTA"


# ── AIP / BagIt + PREMIS (E10 RF-PRE-05/07) ───────────────────────────


def test_aip_builder_artifacts() -> None:
    from app.services.aip_builder import AipDoc, aip_sha256, build_bagit, build_premis

    docs = [AipDoc(document_id="R-1", nombre="oficio.pdf", valor_huella="a" * 64)]
    bagit = build_bagit("EXP-1", docs, NOW)
    premis = build_premis("EXP-1", docs, "b" * 64, NOW)
    assert "BagIt-Version" in bagit and "manifest-sha256" in bagit and "oficio.pdf" in bagit
    assert "urn:orpycamcp:premis:v1" in premis and "messageDigest" in premis
    h1 = aip_sha256(bagit, premis)
    assert len(h1) == 64 and h1 == aip_sha256(bagit, premis)  # determinista


@pytest.mark.asyncio
async def test_empaquetar_aip(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    aip_id = uuid.uuid4()
    # insert_aip (fetchrow) ; insert_evento (fetchrow) — solo el primero alimenta la respuesta
    mock_conn.fetchrow.side_effect = [
        {
            "id": aip_id,
            "expediente_id": "EXP-1",
            "bagit": "...",
            "premis_xml": "<premis/>",
            "aip_sha256": "c" * 64,
            "indice_sha256": None,
            "num_documentos": 1,
            "estado": "empaquetado",
            "created_at": NOW,
        },
        {
            "id": uuid.uuid4(),
            "object_ref": "EXP-1",
            "tipo": "INGESTA",
            "resultado": "ok",
            "hash_before": None,
            "hash_after": "c" * 64,
            "detalle": None,
            "agente": None,
            "ts": NOW,
        },
    ]
    resp = await client.post(
        "/api/v1/preservacion/aip",
        json={
            "expediente_id": "EXP-1",
            "documentos": [
                {"document_id": "R-1", "nombre": "oficio.pdf", "valor_huella": "a" * 64}
            ],
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["num_documentos"] == 1
    assert len(body["aip_sha256"]) == 64


@pytest.mark.asyncio
async def test_get_aip_not_found(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = None
    resp = await client.get("/api/v1/preservacion/aip/EXP-X", headers=TENANT_HEADERS)
    assert resp.status_code == 404
