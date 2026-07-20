# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests de ``POST /api/v1/preservacion/aip`` en modo AIP índice-only (E10 AIP
wiring follow-up, expedientes 100% físicos): ``documentos=[]`` +
``indice_file_id`` presente.

Complementa ``test_aip_real.py`` (que cubre el modo normal, con documentos) y
``test_aip_builder_indice_only.py`` (que ejercita ``build_bag`` directamente,
incl. la validación con la librería de referencia ``bagit``) -- aquí se
ejercita el ROUTER + SERVICE completo, con MinIO/BD mockeados (mismo patrón
que ``test_aip_real.py``, ver su docstring: ningún test escribe objetos
COMPLIANCE reales)."""

from __future__ import annotations

import hashlib
import io
import uuid
import zipfile
from datetime import UTC, datetime

import pytest

import app.core.config as config_module
from app.services.aip_builder import _PREMIS_NS
from tests.conftest import TENANT_HEADERS, TENANT_SLUG, make_file_record, make_worm_row, minio_get_object

_INDICE_CONTENT = b"<indice xmlns='urn:orpycamcp:indice:v1'>expediente fisico, sin anexos</indice>"
_INDICE_SHA256 = hashlib.sha256(_INDICE_CONTENT).hexdigest()


@pytest.fixture(autouse=True)
def _preservation_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module.settings, "preservation_enabled", True)


def _grant_permission(mock_conn) -> None:
    mock_conn.fetchval.return_value = True


def _indice_record(file_id: uuid.UUID) -> dict:
    return make_file_record(
        file_id=file_id,
        filename="indice.xml",
        mime_type="application/xml",
        sha256=_INDICE_SHA256,
        object_key=f"{file_id}/indice.xml",
    )


def _indice_only_payload(*, indice_file_id: uuid.UUID, **overrides) -> dict:
    payload = {
        "expediente_id": "EXP-FISICO-1",
        "documentos": [],
        "indice_file_id": str(indice_file_id),
        "retencion_anios": 5,
        "fecha_inicio": "2026-01-01",
        "legal_hold": False,
    }
    payload.update(overrides)
    return payload


def _zip_from_put_object_call(mock_minio) -> zipfile.ZipFile:
    put_call = mock_minio.put_object.await_args
    stream = put_call.args[2]
    stream.seek(0)
    return zipfile.ZipFile(io.BytesIO(stream.read()))


@pytest.mark.asyncio
async def test_empaquetar_aip_indice_only_happy_path(client_with_conn) -> None:
    """``documentos=[]`` + ``indice_file_id`` -> 201, el índice se sube como
    ÚNICO payload del Bag (``data/indice-firmado.xml``), no como tag file."""
    client, mock_conn, mock_minio = client_with_conn
    indice_file_id = uuid.uuid4()
    aip_id = uuid.uuid4()
    retain_until = datetime(2031, 1, 1, tzinfo=UTC)

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [
        _indice_record(indice_file_id),  # FileRepository.get_by_id_any_state (índice)
        None,  # get_aip_by_expediente_and_sha256 (sin duplicado previo)
        {  # insert_aip RETURNING
            "id": aip_id,
            "expediente_id": "EXP-FISICO-1",
            "premis_xml": "<premis/>",
            "aip_sha256": "c" * 64,
            "indice_sha256": _INDICE_SHA256,
            "num_documentos": 0,
            "estado": "empaquetado",
            "created_at": datetime.now(tz=UTC),
            "bucket": f"orpycamcp-{TENANT_SLUG}-preservation",
            "object_key": f"aip/EXP-FISICO-1/x-{aip_id}.zip",
            "aip_bytes": 999,
            "formato": "application/zip",
            "retain_until": retain_until,
            "retention_mode": "COMPLIANCE",
            "legal_hold": False,
        },
        make_worm_row(expediente_id="EXP-FISICO-1", tipo="aip", retain_until=retain_until),
        {  # insert_evento FIJACION ok
            "id": uuid.uuid4(),
            "object_ref": "EXP-FISICO-1",
            "tipo": "FIJACION",
            "resultado": "ok",
            "hash_before": None,
            "hash_after": "c" * 64,
            "detalle": None,
            "agente": None,
            "ts": datetime.now(tz=UTC),
        },
        {  # insert_evento WORM ok
            "id": uuid.uuid4(),
            "object_ref": "EXP-FISICO-1",
            "tipo": "WORM",
            "resultado": "ok",
            "hash_before": None,
            "hash_after": "c" * 64,
            "detalle": None,
            "agente": None,
            "ts": datetime.now(tz=UTC),
        },
        {  # insert_evento INGESTA ok
            "id": uuid.uuid4(),
            "object_ref": "EXP-FISICO-1",
            "tipo": "INGESTA",
            "resultado": "ok",
            "hash_before": None,
            "hash_after": "c" * 64,
            "detalle": None,
            "agente": None,
            "ts": datetime.now(tz=UTC),
        },
    ]
    mock_minio.get_object = minio_get_object(_INDICE_CONTENT)

    resp = await client.post(
        "/api/v1/preservacion/aip",
        json=_indice_only_payload(indice_file_id=indice_file_id),
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["num_documentos"] == 0
    assert body["indice_sha256"] == _INDICE_SHA256

    zf = _zip_from_put_object_call(mock_minio)
    names = set(zf.namelist())
    assert names == {
        "data/indice-firmado.xml",
        "bagit.txt",
        "bag-info.txt",
        "manifest-sha256.txt",
        "tagmanifest-sha256.txt",
        "premis.xml",
    }
    assert zf.read("data/indice-firmado.xml") == _INDICE_CONTENT

    manifest = zf.read("manifest-sha256.txt").decode()
    assert f"{_INDICE_SHA256}  data/indice-firmado.xml" in manifest
    assert len(manifest.strip().splitlines()) == 1

    bag_info = zf.read("bag-info.txt").decode()
    assert f"Payload-Oxum: {len(_INDICE_CONTENT)}.1" in bag_info

    tagmanifest = zf.read("tagmanifest-sha256.txt").decode()
    assert "data/indice-firmado.xml" not in tagmanifest
    assert "manifest-sha256.txt" in tagmanifest

    premis_xml = zf.read("premis.xml").decode()
    assert premis_xml.count("<object ") == 1  # solo el índice, ningún documento
    assert _INDICE_SHA256 in premis_xml
    assert "data/indice-firmado.xml" in premis_xml  # contentLocation apunta al payload

    from app.services.aip_builder import validate_premis_xml

    validate_premis_xml(premis_xml)  # sigue conforme al XSD oficial

    worm_insert_call = [
        c
        for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_worm_objeto" in c.args[0]
    ][0]
    assert worm_insert_call.args[1] == "EXP-FISICO-1"
    assert worm_insert_call.args[2] == "aip"


@pytest.mark.asyncio
async def test_empaquetar_aip_indice_only_recalculates_fixity_and_blocks_on_mismatch(
    client_with_conn,
) -> None:
    """La fixity del índice SIGUE recalculándose sobre los bytes descargados
    (nunca confiada) también en modo índice-only -- mismo contrato que el
    documento en modo normal (``test_aip_real.py::
    test_empaquetar_aip_fixity_mismatch_blocks_upload``)."""
    client, mock_conn, mock_minio = client_with_conn
    indice_file_id = uuid.uuid4()

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [
        _indice_record(indice_file_id),
        {"id": uuid.uuid4()},  # insert_evento FIJACION fallo
    ]
    mock_minio.get_object = minio_get_object(b"bytes alterados, no coinciden con el sha256 registrado")

    resp = await client.post(
        "/api/v1/preservacion/aip",
        json=_indice_only_payload(indice_file_id=indice_file_id),
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "fixity_mismatch"
    mock_minio.put_object.assert_not_awaited()
    mock_minio.make_bucket.assert_not_awaited()


@pytest.mark.asyncio
async def test_empaquetar_aip_totally_empty_is_422_never_reaches_minio(client_with_conn) -> None:
    """``documentos=[]`` SIN ``indice_file_id`` -- 422 en la capa Pydantic
    (``AipCreate._check_at_least_one_payload``), nunca intenta tocar
    BD/MinIO -- nunca se emitiría un Bag empty-payload."""
    client, mock_conn, mock_minio = client_with_conn
    _grant_permission(mock_conn)

    payload = _indice_only_payload(indice_file_id=uuid.uuid4())
    del payload["indice_file_id"]

    resp = await client.post("/api/v1/preservacion/aip", json=payload, headers=TENANT_HEADERS)

    assert resp.status_code == 422
    mock_conn.fetchrow.assert_not_called()
    mock_minio.put_object.assert_not_awaited()
