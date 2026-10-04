# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`POST /api/v1/internal/documentos/metadata` devuelve el número de radicado y
la tipología (AREA-07-02 / AREA-06-08).

archive-service coteja con esta respuesta lo que declara quien vincula un
radicado a un expediente: el índice electrónico que se firma con XAdES no puede
afirmar un número oficial ni una tipología que escribió el declarante. Sin
`tracking_number` en la respuesta, archive falla cerrado (503): se despliega
ESTE servicio primero.

El contrato se prueba por el cable (router → esquema de respuesta), con el
repositorio doblado devolviendo lo que Postgres devuelve; la consulta real se
prueba en `tests/integration/test_la_metadata_dice_de_donde_viene.py`.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.core import config as cfg_module
from tests.conftest import TENANT_SLUG

_TOKEN = "test-document-internal-token-xxx"
_HEADERS = {"X-Tenant-Slug": TENANT_SLUG, "X-Internal-Token": _TOKEN}
_PATH = "/api/v1/internal/documentos/metadata"


@pytest.fixture(autouse=True)
def _token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg_module.settings, "internal_service_token", _TOKEN)


@pytest.mark.asyncio
async def test_la_respuesta_trae_el_numero_y_la_tipologia_del_radicado(
    client_with_conn,
) -> None:
    client, _ = client_with_conn
    rid = uuid.uuid4()
    resuelto = {
        rid: {
            "subject": "Petición",
            "doc_type": "E",
            "tracking_number": "2026-X-E-000001",
            "doc_class": "COR-PET",
            "origin_dept": "Jurídica",
            "origin_dept_code": 120,
            "formato": "application/pdf",
            "tamano_bytes": 2048,
        }
    }
    with patch(
        "app.routers.internal.DocumentRepository.list_radicado_metadata_internal",
        AsyncMock(return_value=resuelto),
    ):
        resp = await client.post(_PATH, json={"radicado_ids": [str(rid)]}, headers=_HEADERS)

    assert resp.status_code == 200
    item = resp.json()[str(rid)]
    assert item["tracking_number"] == "2026-X-E-000001"
    assert item["doc_class"] == "COR-PET"


@pytest.mark.asyncio
async def test_un_radicado_sin_tipologia_responde_null_y_no_se_inventa_una(
    client_with_conn,
) -> None:
    client, _ = client_with_conn
    rid = uuid.uuid4()
    resuelto = {
        rid: {
            "subject": "s",
            "doc_type": "E",
            "tracking_number": "2026-X-E-000002",
            "doc_class": None,
        }
    }
    with patch(
        "app.routers.internal.DocumentRepository.list_radicado_metadata_internal",
        AsyncMock(return_value=resuelto),
    ):
        resp = await client.post(_PATH, json={"radicado_ids": [str(rid)]}, headers=_HEADERS)

    assert resp.status_code == 200
    assert resp.json()[str(rid)]["doc_class"] is None


@pytest.mark.asyncio
async def test_el_repositorio_pide_y_propaga_tracking_y_doc_class_de_la_tabla_radicados() -> None:
    """El SQL EMITIDO y el mapeo de la fila: el número y la tipología salen de
    `radicados`, no de ningún valor que el llamante pueda pasar, y llegan hasta
    el diccionario que el router devuelve (mutación: `None` en el mapeo)."""
    from app.repositories.document_repository import DocumentRepository

    rid = uuid.uuid4()
    conn = AsyncMock()
    conn.fetch.return_value = [
        {
            "id": rid,
            "tracking_number": "2026-X-E-000009",
            "subject": "s",
            "doc_type": "E",
            "doc_class": "COR-PET",
            "origin_dept": None,
            "origin_dept_code": None,
            "formato": None,
            "tamano_bytes": None,
        }
    ]
    meta = await DocumentRepository(conn, TENANT_SLUG).list_radicado_metadata_internal(
        [rid], max_clearance=3
    )

    sql = conn.fetch.await_args.args[0]
    assert "r.tracking_number" in sql
    assert "r.doc_class" in sql
    assert meta[rid]["tracking_number"] == "2026-X-E-000009"
    assert meta[rid]["doc_class"] == "COR-PET"
