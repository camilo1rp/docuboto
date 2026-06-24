# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del índice electrónico (E15, Acuerdo AGN 001/2024)."""

import uuid
from datetime import UTC, datetime

import pytest

from app.services.index_builder import (
    IndiceData,
    IndiceItemData,
    build_xml,
    content_hash,
)
from tests.conftest import TENANT_HEADERS, make_expediente_record, make_radicado_link_record

NOW = datetime.now(tz=UTC)


def _indice_row(*, version=1, estado="vigente", content="x" * 64, num=1) -> dict:
    return {
        "id": uuid.uuid4(),
        "expediente_id": uuid.uuid4(),
        "version": version,
        "xml": "<indice/>",
        "xml_sha256": "a" * 64,
        "content_hash": content,
        "algoritmo": "SHA-256",
        "xsd_version": "v1",
        "num_documentos": num,
        "estado": estado,
        "firma_id": None,
        "minio_key_firmado": None,
        "generado_por": "system",
        "generado_ts": NOW,
    }


# -- IndexBuilder (unidad, sin BD) ------------------------------------


def test_build_xml_has_normative_fields() -> None:
    data = IndiceData(
        expediente_id=str(uuid.uuid4()),
        code="EXP-2024-0001",
        name="Contrato",
        serie_id=str(uuid.uuid4()),
        fecha_creacion=NOW,
        items=[IndiceItemData(document_id=str(uuid.uuid4()), orden=1, titulo="2024-E-001")],
    )
    xml = build_xml(data)
    assert "urn:orpycamcp:indice:v1" in xml
    assert "identificadorSistema" in xml
    assert "valorHuella" in xml and "funcionResumen" in xml
    assert "<" in xml and "documento" in xml


def test_content_hash_is_order_sensitive() -> None:
    a = IndiceItemData(document_id="11111111-1111-1111-1111-111111111111", orden=1)
    b = IndiceItemData(document_id="22222222-2222-2222-2222-222222222222", orden=2)
    assert content_hash([a, b]) != content_hash([b, a])
    assert content_hash([a, b]) == content_hash([a, b])  # determinista


# -- Endpoints --------------------------------------------------------


@pytest.mark.asyncio
async def test_get_indice_current(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = _indice_row(version=2, num=1)
    mock_conn.fetch.return_value = [
        {
            "document_id": uuid.uuid4(),
            "orden": 1,
            "titulo": "2024-E-001",
            "tipologia": None,
            "fecha_declaracion": None,
            "fecha_archivado": NOW,
            "valor_huella": None,
            "funcion_resumen": "SHA-256",
            "formato": None,
            "tamano_bytes": None,
        },
    ]
    resp = await client.get(f"/api/v1/expedientes/{uuid.uuid4()}/indice", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 2
    assert len(body["items"]) == 1


@pytest.mark.asyncio
async def test_get_indice_not_found(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None
    resp = await client.get(f"/api/v1/expedientes/{uuid.uuid4()}/indice", headers=TENANT_HEADERS)
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "expediente_indice_not_found"


def test_item_valor_huella_from_link_hash() -> None:
    """El content_hash del vínculo se usa como valor_huella del item del índice."""
    from app.models.archive import Expediente
    from app.services.index_service import IndexService

    h = "c" * 64
    rad = make_radicado_link_record(content_hash=h)
    exp = Expediente(
        id=uuid.uuid4(),
        code="EXP",
        name="x",
        description=None,
        trd_serie_id=None,
        status="open",
        opened_by=None,
        opened_at=NOW,
        closed_at=None,
        updated_at=NOW,
        radicados=[
            type(
                "R",
                (),
                {
                    "radicado_id": rad["radicado_id"],
                    "tracking_number": rad["tracking_number"],
                    "added_at": NOW,
                    "content_hash": h,
                },
            )()
        ],
    )
    items = IndexService.__new__(IndexService)._items_from(exp)
    assert items[0].valor_huella == h


@pytest.mark.asyncio
async def test_link_to_closed_expediente_rejected(client_with_conn) -> None:
    """No se pueden añadir documentos a un expediente cerrado (inmutabilidad)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = make_expediente_record(exp_status="closed")
    mock_conn.fetch.return_value = []
    mock_conn.fetchval.return_value = 0

    resp = await client.post(
        f"/api/v1/expedientes/{uuid.uuid4()}/radicados",
        json={"radicado_id": str(uuid.uuid4()), "tracking_number": "2024-E-009"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "expediente_not_open"


@pytest.mark.asyncio
async def test_verify_intact(client_with_conn) -> None:
    """Si la huella del índice coincide con la actual del documento → válido."""
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    rad_id = uuid.uuid4()
    h = "d" * 64
    indice = _indice_row(version=2, num=1)
    item = {
        "document_id": rad_id,
        "orden": 1,
        "titulo": "2024-E-001",
        "tipologia": None,
        "fecha_declaracion": None,
        "fecha_archivado": NOW,
        "valor_huella": h,
        "funcion_resumen": "SHA-256",
        "formato": None,
        "tamano_bytes": None,
    }
    exp = make_expediente_record(exp_id=exp_id)
    link = make_radicado_link_record(radicado_id=rad_id, content_hash=h)
    # fetchrow: get_current(indice), get_expediente(exp); fetch: items, radicados
    mock_conn.fetchrow.side_effect = [indice, exp]
    mock_conn.fetch.side_effect = [[item], [link]]

    resp = await client.get(f"/api/v1/expedientes/{exp_id}/indice/verify", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["valido"] is True
    assert body["documentos"][0]["coincide"] is True
    assert body["firma"]["presente"] is False


@pytest.mark.asyncio
async def test_verify_tampered(client_with_conn) -> None:
    """Si el documento cambió (hash distinto al del índice) → inválido."""
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    rad_id = uuid.uuid4()
    item = {
        "document_id": rad_id,
        "orden": 1,
        "titulo": "2024-E-001",
        "tipologia": None,
        "fecha_declaracion": None,
        "fecha_archivado": NOW,
        "valor_huella": "d" * 64,
        "funcion_resumen": "SHA-256",
        "formato": None,
        "tamano_bytes": None,
    }
    exp = make_expediente_record(exp_id=exp_id)
    link = make_radicado_link_record(radicado_id=rad_id, content_hash="e" * 64)
    mock_conn.fetchrow.side_effect = [_indice_row(version=2, num=1), exp]
    mock_conn.fetch.side_effect = [[item], [link]]

    resp = await client.get(f"/api/v1/expedientes/{exp_id}/indice/verify", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["valido"] is False
    assert body["documentos"][0]["coincide"] is False


@pytest.mark.asyncio
async def test_rebuild_idempotent_when_unchanged(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    rad_id = uuid.uuid4()
    # content_hash del único documento (orden 1, sin huella) que el índice ya tiene.
    expected_ch = content_hash([IndiceItemData(document_id=str(rad_id), orden=1)])
    exp = make_expediente_record(exp_id=exp_id)
    # get_expediente_by_id: fetchrow(exp) + fetch(radicados); get_current: fetchrow(indice)
    mock_conn.fetchrow.side_effect = [exp, _indice_row(version=3, content=expected_ch)]
    mock_conn.fetch.return_value = [
        make_radicado_link_record(radicado_id=rad_id, tracking_number="2024-E-001")
    ]

    resp = await client.post(f"/api/v1/expedientes/{exp_id}/indice/rebuild", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["unchanged"] is True
    assert body["version"] == 3
