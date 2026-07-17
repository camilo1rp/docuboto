# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests de POST /api/v1/preservacion/aip — AIP real (E10 Increment B, BagIt +
PREMIS v3, RF-PRE-05/07).

Ningún test escribe objetos COMPLIANCE reales — MinIO está completamente
mockeado (`tests/conftest.py::_make_mock_minio`); solo se asertan las
LLAMADAS (make_bucket, put_object, etc.) y los BYTES que se les pasan
(`io.BytesIO` reales en memoria, nunca I/O contra un MinIO real).
"""

from __future__ import annotations

import hashlib
import io
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.core.config as config_module
from app.services.aip_builder import _PREMIS_NS, AipDocMaterial
from tests.conftest import (
    INTERNAL_HEADERS,
    TENANT_HEADERS,
    TENANT_SLUG,
    make_file_record,
    make_worm_row,
    minio_get_object,
)

_DOC_CONTENT = b"contenido real del oficio radicado"
_DOC_SHA256 = hashlib.sha256(_DOC_CONTENT).hexdigest()
_INDICE_CONTENT = b"<indice>firmado</indice>"
_INDICE_SHA256 = hashlib.sha256(_INDICE_CONTENT).hexdigest()


@pytest.fixture(autouse=True)
def _preservation_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module.settings, "preservation_enabled", True)


def _grant_permission(mock_conn) -> None:
    mock_conn.fetchval.return_value = True


def _deny_permission(mock_conn) -> None:
    mock_conn.fetchval.return_value = False


def _doc_record(file_id: uuid.UUID) -> dict:
    return make_file_record(
        file_id=file_id,
        filename="oficio.pdf",
        mime_type="application/pdf",
        sha256=_DOC_SHA256,
        object_key=f"{file_id}/oficio.pdf",
    )


def _indice_record(file_id: uuid.UUID) -> dict:
    return make_file_record(
        file_id=file_id,
        filename="indice.xml",
        mime_type="application/xml",
        sha256=_INDICE_SHA256,
        object_key=f"{file_id}/indice.xml",
    )


def _base_payload(*, doc_file_id: uuid.UUID, indice_file_id: uuid.UUID | None = None, **overrides) -> dict:
    payload = {
        "expediente_id": "EXP-1",
        "documentos": [
            {"document_id": "R-1", "nombre": "oficio.pdf", "file_id": str(doc_file_id)}
        ],
        "retencion_anios": 5,
        "fecha_inicio": "2026-01-01",
        "legal_hold": False,
    }
    if indice_file_id is not None:
        payload["indice_file_id"] = str(indice_file_id)
    payload.update(overrides)
    return payload


def _zip_from_put_object_call(mock_minio) -> zipfile.ZipFile:
    put_call = mock_minio.put_object.await_args
    stream = put_call.args[2]
    stream.seek(0)
    return zipfile.ZipFile(io.BytesIO(stream.read()))


def _fake_minio_object(content: bytes) -> MagicMock:
    """El objeto devuelto por `await minio.get_object(...)` (no el mock del
    método en sí) — para usar en `side_effect=[...]` cuando se descargan
    varios objetos en una misma llamada HTTP (documento + índice)."""
    obj = MagicMock()
    obj.read = AsyncMock(return_value=content)
    obj.close = MagicMock()
    return obj


@pytest.mark.asyncio
async def test_empaquetar_aip_happy_path_bag_and_premis(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    doc_file_id = uuid.uuid4()
    indice_file_id = uuid.uuid4()
    aip_id = uuid.uuid4()
    retain_until = datetime(2031, 1, 1, tzinfo=UTC)

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [
        _doc_record(doc_file_id),  # FileRepository.get_by_id_any_state (doc)
        _indice_record(indice_file_id),  # get_by_id_any_state (indice)
        {  # insert_aip RETURNING
            "id": aip_id,
            "expediente_id": "EXP-1",
            "premis_xml": "<premis/>",
            "aip_sha256": "c" * 64,
            "indice_sha256": _INDICE_SHA256,
            "num_documentos": 1,
            "estado": "empaquetado",
            "created_at": datetime.now(tz=UTC),
            "bucket": f"orpycamcp-{TENANT_SLUG}-preservation",
            "object_key": f"aip/EXP-1/x-{aip_id}.zip",
            "aip_bytes": 999,
            "formato": "application/zip",
            "retain_until": retain_until,
            "retention_mode": "COMPLIANCE",
            "legal_hold": False,
        },
        make_worm_row(  # worm_repo.insert RETURNING
            expediente_id="EXP-1", tipo="aip", retain_until=retain_until
        ),
        {  # insert_evento FIJACION ok
            "id": uuid.uuid4(),
            "object_ref": "EXP-1",
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
            "object_ref": "EXP-1",
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
            "object_ref": "EXP-1",
            "tipo": "INGESTA",
            "resultado": "ok",
            "hash_before": None,
            "hash_after": "c" * 64,
            "detalle": None,
            "agente": None,
            "ts": datetime.now(tz=UTC),
        },
    ]
    mock_minio.get_object = AsyncMock(
        side_effect=[_fake_minio_object(_DOC_CONTENT), _fake_minio_object(_INDICE_CONTENT)]
    )

    resp = await client.post(
        "/api/v1/preservacion/aip",
        json=_base_payload(doc_file_id=doc_file_id, indice_file_id=indice_file_id),
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["bucket"] == f"orpycamcp-{TENANT_SLUG}-preservation"
    assert body["retention_mode"] == "COMPLIANCE"
    assert body["formato"] == "application/zip"
    assert body["legal_hold"] is False

    # --- Retención pasada a MinIO ---
    put_call = mock_minio.put_object.await_args
    assert put_call.args[0] == f"orpycamcp-{TENANT_SLUG}-preservation"
    retention = put_call.kwargs["retention"]
    assert retention.mode == "COMPLIANCE"
    assert retention.retain_until_date == retain_until
    assert put_call.kwargs["content_type"] == "application/zip"

    # --- Bag REAL bien formado ---
    zf = _zip_from_put_object_call(mock_minio)
    names = set(zf.namelist())
    assert names == {
        "data/oficio.pdf",
        "bagit.txt",
        "bag-info.txt",
        "manifest-sha256.txt",
        "tagmanifest-sha256.txt",
        "premis.xml",
        "indice-firmado.xml",
    }
    assert zf.read("data/oficio.pdf") == _DOC_CONTENT
    assert zf.read("indice-firmado.xml") == _INDICE_CONTENT

    bagit_txt = zf.read("bagit.txt").decode()
    assert "BagIt-Version: 1.0" in bagit_txt
    assert "Tag-File-Character-Encoding: UTF-8" in bagit_txt

    manifest = zf.read("manifest-sha256.txt").decode()
    assert f"{_DOC_SHA256}  data/oficio.pdf" in manifest
    assert "indice-firmado.xml" not in manifest  # el índice es tag file, no payload

    bag_info = zf.read("bag-info.txt").decode()
    assert f"Payload-Oxum: {len(_DOC_CONTENT)}.1" in bag_info
    assert "External-Identifier: EXP-1" in bag_info

    tagmanifest = zf.read("tagmanifest-sha256.txt").decode()
    assert "premis.xml" in tagmanifest
    assert "bagit.txt" in tagmanifest
    assert "bag-info.txt" in tagmanifest
    assert "indice-firmado.xml" in tagmanifest
    # manifest-sha256.txt ES UN tag file (RFC 8493 §2.2.1) — su fixity debe
    # quedar cubierta por el tagmanifest, no solo la de bagit.txt/premis.xml
    # (hallazgo de auditoría Increment B).
    assert "manifest-sha256.txt" in tagmanifest

    # --- PREMIS v3 conforme: namespace oficial + object/event/agent ---
    premis_xml = zf.read("premis.xml").decode()
    assert _PREMIS_NS == "http://www.loc.gov/premis/v3"
    assert _PREMIS_NS in premis_xml
    assert premis_xml.count("<object") >= 2  # documento + índice
    assert "<event" in premis_xml
    assert "<agent" in premis_xml
    assert _DOC_SHA256 in premis_xml
    assert _INDICE_SHA256 in premis_xml

    from app.services.aip_builder import validate_premis_xml

    validate_premis_xml(premis_xml)  # no debe lanzar

    # --- aip_sha256 == sha256 del ZIP completo (no del texto sintético del stub) ---
    stream = put_call.args[2]
    stream.seek(0)
    zip_bytes = stream.read()
    assert put_call.kwargs["length"] == len(zip_bytes)

    insert_aip_call = [
        c
        for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO aip" in c.args[0]
    ][0]
    # insert_aip() params: (sql, id, expediente_id, premis_xml, aip_sha256, ...)
    assert insert_aip_call.args[4] == hashlib.sha256(zip_bytes).hexdigest()

    # --- WORM: registrado con tipo="aip" ---
    worm_insert_call = [
        c
        for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_worm_objeto" in c.args[0]
    ][0]
    # insert() params: (sql, expediente_id, tipo, bucket, object_key, ...)
    assert worm_insert_call.args[1] == "EXP-1"
    assert worm_insert_call.args[2] == "aip"

    # --- rastro de eventos PREMIS: FIJACION ok + WORM ok + INGESTA ok, en ese
    # orden — antes solo el camino de FALLO de fijación quedaba en
    # `preservacion_evento` (hallazgo de auditoría Increment B, PIEZA 5) ---
    evento_inserts = [
        c
        for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_evento" in c.args[0]
    ]
    assert len(evento_inserts) == 3
    # insert_evento() params: (sql, object_ref, tipo, resultado, hash_before, hash_after, detalle, agente)
    assert [c.args[2] for c in evento_inserts] == ["FIJACION", "WORM", "INGESTA"]
    assert all(c.args[3] == "ok" for c in evento_inserts)

    # --- audit_log inmutable ---
    audit_inserts = [
        c
        for c in mock_conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_inserts) == 1
    assert audit_inserts[0].args[5] == "aip.empaquetado"
    assert audit_inserts[0].args[6] == "aip"
    assert audit_inserts[0].args[7] == "EXP-1"


@pytest.mark.asyncio
async def test_empaquetar_aip_fixity_mismatch_blocks_upload(client_with_conn) -> None:
    """Un objeto cuyos bytes REALES no coinciden con `files.sha256` aborta
    TODO el empaquetado — no se sube nada al bucket WORM (PIEZA 2 (b))."""
    client, mock_conn, mock_minio = client_with_conn
    doc_file_id = uuid.uuid4()

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [
        _doc_record(doc_file_id),  # get_by_id_any_state
        {"id": uuid.uuid4()},  # insert_evento FIJACION fallo
    ]
    mock_minio.get_object = minio_get_object(b"bytes alterados, no coinciden")

    resp = await client.post(
        "/api/v1/preservacion/aip",
        json=_base_payload(doc_file_id=doc_file_id),
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "fixity_mismatch"
    mock_minio.put_object.assert_not_awaited()
    mock_minio.make_bucket.assert_not_awaited()

    insert_evt_call = mock_conn.fetchrow.call_args_list[-1]
    assert "INSERT INTO preservacion_evento" in insert_evt_call.args[0]
    assert insert_evt_call.args[3] == "fallo"

    audit_inserts = [
        c
        for c in mock_conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_inserts) == 1
    assert audit_inserts[0].args[5] == "file.fixity_fallo"


@pytest.mark.asyncio
async def test_empaquetar_aip_forbidden_without_permission(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    _deny_permission(mock_conn)

    resp = await client.post(
        "/api/v1/preservacion/aip",
        json=_base_payload(doc_file_id=uuid.uuid4()),
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 403
    mock_minio.put_object.assert_not_awaited()


@pytest.mark.asyncio
async def test_empaquetar_aip_file_not_found(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    _grant_permission(mock_conn)
    mock_conn.fetchrow.return_value = None

    resp = await client.post(
        "/api/v1/preservacion/aip",
        json=_base_payload(doc_file_id=uuid.uuid4()),
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "file_not_found"


@pytest.mark.asyncio
async def test_empaquetar_aip_disabled_by_feature_flag(client_with_conn, monkeypatch) -> None:
    client, mock_conn, mock_minio = client_with_conn
    _grant_permission(mock_conn)
    monkeypatch.setattr(config_module.settings, "preservation_enabled", False)

    resp = await client.post(
        "/api/v1/preservacion/aip",
        json=_base_payload(doc_file_id=uuid.uuid4()),
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "preservation_disabled"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_empaquetar_aip_rejects_retention_beyond_max_years(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    _grant_permission(mock_conn)
    too_far = (datetime.now(tz=UTC) + timedelta(days=365 * 200)).isoformat()

    resp = await client.post(
        "/api/v1/preservacion/aip",
        json=_base_payload(
            doc_file_id=uuid.uuid4(),
            retencion_anios=None,
            fecha_inicio=None,
            retain_until=too_far,
        ),
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_retention"
    mock_conn.fetchrow.assert_not_called()  # falla antes de tocar archivos/MinIO


@pytest.mark.asyncio
async def test_empaquetar_aip_requires_at_least_one_documento(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    _grant_permission(mock_conn)

    payload = _base_payload(doc_file_id=uuid.uuid4())
    payload["documentos"] = []

    resp = await client.post(
        "/api/v1/preservacion/aip",
        json=payload,
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "nombre",
    [
        "../etc/passwd",  # traversal
        "sub/oficio.pdf",  # separador de ruta fuera de data/
        "oficio\\pdf",  # backslash
        "oficio\r\n.pdf",  # CR/LF — inyectaría una línea falsa en el manifiesto
        "..",
    ],
)
async def test_empaquetar_aip_rejects_unsafe_nombre(client_with_conn, nombre: str) -> None:
    """`nombre` es la ruta de payload literal dentro del Bag (`data/{nombre}`)
    y una línea de `manifest-sha256.txt` — separadores de ruta y caracteres
    de control se rechazan en el borde Pydantic (hallazgo de auditoría
    Increment B: zip-slip / inyección de líneas de manifiesto)."""
    client, mock_conn, mock_minio = client_with_conn
    _grant_permission(mock_conn)

    payload = _base_payload(doc_file_id=uuid.uuid4())
    payload["documentos"][0]["nombre"] = nombre

    resp = await client.post("/api/v1/preservacion/aip", json=payload, headers=TENANT_HEADERS)

    assert resp.status_code == 422
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_empaquetar_aip_rejects_duplicate_nombre(client_with_conn) -> None:
    """Dos documentos con el mismo `nombre` colisionarían en
    `files[f"data/{nombre}"]` dentro del Bag — uno se perdería SILENCIOSAMENTE
    mientras `num_files`/`Payload-Oxum`/PREMIS seguirían contando ambos
    (hallazgo de auditoría Increment B). Se rechaza en el borde Pydantic."""
    client, mock_conn, mock_minio = client_with_conn
    _grant_permission(mock_conn)

    payload = _base_payload(doc_file_id=uuid.uuid4())
    payload["documentos"].append(
        {"document_id": "R-2", "nombre": "oficio.pdf", "file_id": str(uuid.uuid4())}
    )

    resp = await client.post("/api/v1/preservacion/aip", json=payload, headers=TENANT_HEADERS)

    assert resp.status_code == 422
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_empaquetar_aip_without_indice_excludes_indice_tag_file(client_with_conn) -> None:
    """Sin `indice_file_id`, el Bag NO incluye `indice-firmado.xml` ni un
    segundo OBJECT en el PREMIS — el índice es estrictamente opcional."""
    client, mock_conn, mock_minio = client_with_conn
    doc_file_id = uuid.uuid4()
    aip_id = uuid.uuid4()
    retain_until = datetime(2031, 1, 1, tzinfo=UTC)

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [
        _doc_record(doc_file_id),  # get_by_id_any_state (doc)
        {  # insert_aip RETURNING
            "id": aip_id,
            "expediente_id": "EXP-1",
            "premis_xml": "<premis/>",
            "aip_sha256": "c" * 64,
            "indice_sha256": None,
            "num_documentos": 1,
            "estado": "empaquetado",
            "created_at": datetime.now(tz=UTC),
            "bucket": f"orpycamcp-{TENANT_SLUG}-preservation",
            "object_key": f"aip/EXP-1/x-{aip_id}.zip",
            "aip_bytes": 999,
            "formato": "application/zip",
            "retain_until": retain_until,
            "retention_mode": "COMPLIANCE",
            "legal_hold": False,
        },
        make_worm_row(expediente_id="EXP-1", tipo="aip", retain_until=retain_until),
        {  # insert_evento FIJACION ok
            "id": uuid.uuid4(),
            "object_ref": "EXP-1",
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
            "object_ref": "EXP-1",
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
            "object_ref": "EXP-1",
            "tipo": "INGESTA",
            "resultado": "ok",
            "hash_before": None,
            "hash_after": "c" * 64,
            "detalle": None,
            "agente": None,
            "ts": datetime.now(tz=UTC),
        },
    ]
    mock_minio.get_object = minio_get_object(_DOC_CONTENT)

    resp = await client.post(
        "/api/v1/preservacion/aip",
        json=_base_payload(doc_file_id=doc_file_id),  # sin indice_file_id
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    zf = _zip_from_put_object_call(mock_minio)
    names = set(zf.namelist())
    assert "indice-firmado.xml" not in names
    assert names == {
        "data/oficio.pdf",
        "bagit.txt",
        "bag-info.txt",
        "manifest-sha256.txt",
        "tagmanifest-sha256.txt",
        "premis.xml",
    }
    tagmanifest = zf.read("tagmanifest-sha256.txt").decode()
    assert "indice-firmado.xml" not in tagmanifest

    premis_xml = zf.read("premis.xml").decode()
    # `<object ` (con espacio) para no contar `<objectIdentifier`/`<objectCharacteristics`
    # — cada `object` real lleva el atributo `xsi:type="premis:file"`.
    assert premis_xml.count("<object ") == 1  # solo el documento, sin el índice

    from app.services.aip_builder import validate_premis_xml

    validate_premis_xml(premis_xml)  # sigue conforme al XSD sin el índice


@pytest.mark.asyncio
async def test_empaquetar_aip_multiple_documentos_payload_oxum_and_manifest(
    client_with_conn,
) -> None:
    """Con >1 documento, `Payload-Oxum` refleja bytes y CANTIDAD reales (no un
    valor fijo/simulado), y `manifest-sha256.txt` trae una línea por archivo
    con su ruta `data/` real — cubre el caso de expedientes con varios anexos."""
    client, mock_conn, mock_minio = client_with_conn
    doc1_id = uuid.uuid4()
    doc2_id = uuid.uuid4()
    aip_id = uuid.uuid4()
    retain_until = datetime(2031, 1, 1, tzinfo=UTC)

    content2 = b"segundo documento del expediente, mas largo que el primero"
    sha2 = hashlib.sha256(content2).hexdigest()

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [
        _doc_record(doc1_id),  # get_by_id_any_state (doc 1)
        make_file_record(
            file_id=doc2_id,
            filename="anexo2.pdf",
            mime_type="application/pdf",
            sha256=sha2,
            object_key=f"{doc2_id}/anexo2.pdf",
        ),  # get_by_id_any_state (doc 2)
        {  # insert_aip RETURNING
            "id": aip_id,
            "expediente_id": "EXP-1",
            "premis_xml": "<premis/>",
            "aip_sha256": "c" * 64,
            "indice_sha256": None,
            "num_documentos": 2,
            "estado": "empaquetado",
            "created_at": datetime.now(tz=UTC),
            "bucket": f"orpycamcp-{TENANT_SLUG}-preservation",
            "object_key": f"aip/EXP-1/x-{aip_id}.zip",
            "aip_bytes": 999,
            "formato": "application/zip",
            "retain_until": retain_until,
            "retention_mode": "COMPLIANCE",
            "legal_hold": False,
        },
        make_worm_row(expediente_id="EXP-1", tipo="aip", retain_until=retain_until),
        {  # insert_evento FIJACION ok
            "id": uuid.uuid4(),
            "object_ref": "EXP-1",
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
            "object_ref": "EXP-1",
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
            "object_ref": "EXP-1",
            "tipo": "INGESTA",
            "resultado": "ok",
            "hash_before": None,
            "hash_after": "c" * 64,
            "detalle": None,
            "agente": None,
            "ts": datetime.now(tz=UTC),
        },
    ]
    mock_minio.get_object = AsyncMock(
        side_effect=[_fake_minio_object(_DOC_CONTENT), _fake_minio_object(content2)]
    )

    payload = _base_payload(doc_file_id=doc1_id)
    payload["documentos"].append(
        {"document_id": "R-2", "nombre": "anexo2.pdf", "file_id": str(doc2_id)}
    )

    resp = await client.post(
        "/api/v1/preservacion/aip",
        json=payload,
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    zf = _zip_from_put_object_call(mock_minio)

    total_bytes = len(_DOC_CONTENT) + len(content2)
    bag_info = zf.read("bag-info.txt").decode()
    assert f"Payload-Oxum: {total_bytes}.2" in bag_info

    manifest = zf.read("manifest-sha256.txt").decode()
    assert f"{_DOC_SHA256}  data/oficio.pdf" in manifest
    assert f"{sha2}  data/anexo2.pdf" in manifest
    assert len(manifest.strip().splitlines()) == 2

    names = set(zf.namelist())
    assert "data/oficio.pdf" in names
    assert "data/anexo2.pdf" in names
    assert zf.read("data/anexo2.pdf") == content2

    premis_xml = zf.read("premis.xml").decode()
    assert premis_xml.count("<object ") == 2
    assert sha2 in premis_xml


@pytest.mark.asyncio
async def test_empaquetar_aip_premis_invalid_blocks_upload(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Si el PREMIS generado NO valida contra el XSD (regresión hipotética en
    el builder), `build_bag` aborta con `PremisValidationError` -> 500
    `premis_invalid` y NO se sube nada al bucket WORM (red de seguridad de
    `assertValid`, nunca un PDI inválido llega a MinIO)."""
    client, mock_conn, mock_minio = client_with_conn
    doc_file_id = uuid.uuid4()

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [_doc_record(doc_file_id)]
    mock_minio.get_object = minio_get_object(_DOC_CONTENT)

    import app.services.aip_builder as aip_builder_module

    def _boom(xml_text: str) -> None:
        raise aip_builder_module.PremisValidationError("XSD roto a propósito para el test")

    monkeypatch.setattr(aip_builder_module, "validate_premis_xml", _boom)

    resp = await client.post(
        "/api/v1/preservacion/aip",
        json=_base_payload(doc_file_id=doc_file_id),
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 500
    assert resp.json()["detail"]["error"] == "premis_invalid"
    mock_minio.put_object.assert_not_awaited()
    mock_minio.make_bucket.assert_not_awaited()
    mock_conn.fetchrow.assert_called_once()  # ni insert_aip ni worm_repo.insert llegaron a correr


@pytest.mark.asyncio
async def test_get_aip_returns_real_location_fields(client_with_conn) -> None:
    """E10 Increment C: `GET /aip/{id}` ya NO se gatea por
    `USUA_PERM_EXPEDIENTE` (storage-service no conoce el `nivel_seguridad`
    del expediente) — es un endpoint INTERNO (X-Internal-Token, D-02); el
    no-read-up real vive en archive-service (`GET /expedientes/{id}/aip`,
    ver `test_aip_internal_gate.py` para el gate en sí)."""
    client, mock_conn, mock_minio = client_with_conn
    aip_id = uuid.uuid4()
    retain_until = datetime(2031, 1, 1, tzinfo=UTC)
    mock_conn.fetchrow.return_value = {
        "id": aip_id,
        "expediente_id": "EXP-1",
        "premis_xml": "<premis/>",
        "aip_sha256": "d" * 64,
        "indice_sha256": None,
        "num_documentos": 1,
        "estado": "empaquetado",
        "created_at": datetime.now(tz=UTC),
        "bucket": f"orpycamcp-{TENANT_SLUG}-preservation",
        "object_key": f"aip/EXP-1/x-{aip_id}.zip",
        "aip_bytes": 2048,
        "formato": "application/zip",
        "retain_until": retain_until,
        "retention_mode": "COMPLIANCE",
        "legal_hold": False,
    }

    resp = await client.get("/api/v1/preservacion/aip/EXP-1", headers=INTERNAL_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert body["bucket"] == f"orpycamcp-{TENANT_SLUG}-preservation"
    assert body["aip_bytes"] == 2048
    assert body["formato"] == "application/zip"


@pytest.mark.asyncio
async def test_get_aip_without_internal_token_is_401(client_with_conn) -> None:
    """El PREMIS embebido inventaría nombres/SHA-256/tamaños de TODOS los
    documentos del expediente — sin X-Internal-Token, 401 ANTES de tocar la
    BD (D-02, E10 Increment C: reemplaza el gate por-permiso, que no conocía
    el `nivel_seguridad` del expediente, RF-SEG-08)."""
    client, mock_conn, mock_minio = client_with_conn

    resp = await client.get("/api/v1/preservacion/aip/EXP-1", headers=TENANT_HEADERS)

    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "missing_internal_token"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_get_aip_stub_row_returns_200_with_null_fields(client_with_conn) -> None:
    """El AIP vigente de un expediente puede seguir siendo un stub del
    Increment A (migración 007 aditiva/nullable): `bucket`/`object_key`/
    `aip_bytes`/`retain_until`/`retention_mode` quedan NULL en esas filas.
    Antes `AipResponse` los declaraba NOT NULL y esto lanzaba un 500
    (hallazgo de auditoría Increment B)."""
    client, mock_conn, mock_minio = client_with_conn
    aip_id = uuid.uuid4()
    mock_conn.fetchrow.return_value = {
        "id": aip_id,
        "expediente_id": "EXP-1",
        "premis_xml": "=== bagit.txt ===\nstub",
        "aip_sha256": "e" * 64,
        "indice_sha256": None,
        "num_documentos": 1,
        "estado": "empaquetado",
        "created_at": datetime.now(tz=UTC),
        "bucket": None,
        "object_key": None,
        "aip_bytes": None,
        "formato": "application/zip",
        "retain_until": None,
        "retention_mode": None,
        "legal_hold": False,
    }

    resp = await client.get("/api/v1/preservacion/aip/EXP-1", headers=INTERNAL_HEADERS)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["bucket"] is None
    assert body["object_key"] is None
    assert body["aip_bytes"] is None
    assert body["retain_until"] is None
    assert body["retention_mode"] is None


# --- Defensa en profundidad del builder (app.services.aip_builder) ---------
#
# La capa Pydantic (`AipCreate`/`AipDocInput`) ya cierra `nombre` duplicado/
# inseguro en el borde HTTP (ver los tests `test_empaquetar_aip_rejects_*`
# arriba). Los tests siguientes ejercen `build_bag` DIRECTAMENTE — sin pasar
# por el router — para probar que el builder en sí mismo (invocable desde
# cualquier otro caller futuro) nunca infla `num_files`/`Payload-Oxum` por
# encima de lo que el ZIP contiene de verdad, y que escapa CR/LF/`%` en las
# rutas del manifiesto (RFC 8493 §2.1.3), aunque la Pydantic de arriba jamás
# le pase esos valores hoy.


def _material(nombre: str, content: bytes, *, document_id: str = "R-1") -> AipDocMaterial:
    return AipDocMaterial(
        document_id=document_id,
        nombre=nombre,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        mime_type="application/pdf",
    )


def test_manifest_sha256_txt_percent_encodes_cr_lf_and_percent() -> None:
    from app.services.aip_builder import manifest_sha256_txt

    doc = _material("100%.pdf", b"x")
    manifest = manifest_sha256_txt([doc]).decode()

    assert "data/100%25.pdf" in manifest
    # nunca el '%' crudo seguido de un dígito que no sea la propia
    # codificación (no debe quedar 'data/100%.pdf' sin escapar)
    assert "data/100%.pdf" not in manifest


def test_build_bag_derives_num_files_and_oxum_from_real_data_entries() -> None:
    """Si dos `AipDocMaterial` comparten `nombre` (bypass hipotético de la
    validación Pydantic — p. ej. una llamada directa al builder), el segundo
    SOBRESCRIBE al primero en `files["data/{nombre}"]`; `num_files` y
    `Payload-Oxum` deben reflejar la ÚNICA entrada real, no `len(docs)`
    (hallazgo de auditoría Increment B)."""
    from app.services.aip_builder import build_bag

    doc1 = _material("oficio.pdf", b"contenido uno", document_id="R-1")
    doc2 = _material("oficio.pdf", b"contenido dos, mas largo", document_id="R-2")

    artifact = build_bag(
        expediente_id="EXP-1",
        tenant_slug=TENANT_SLUG,
        docs=[doc1, doc2],
        indice=None,
        generado=datetime.now(tz=UTC),
        actor=None,
    )

    assert artifact.num_files == 1  # una sola entrada real en data/, no 2
    assert artifact.total_bytes == len(b"contenido dos, mas largo")

    zf = zipfile.ZipFile(io.BytesIO(artifact.zip_bytes))
    assert zf.read("data/oficio.pdf") == b"contenido dos, mas largo"
    bag_info = zf.read("bag-info.txt").decode()
    assert f"Payload-Oxum: {len(b'contenido dos, mas largo')}.1" in bag_info


def test_build_bag_tagmanifest_covers_payload_manifest() -> None:
    """`tagmanifest-sha256.txt` debe cubrir `manifest-sha256.txt` (RFC 8493
    §2.2.1: el manifiesto de payload ES UN tag file) — invocación directa del
    builder, sin pasar por el router."""
    from app.services.aip_builder import build_bag

    doc = _material("oficio.pdf", b"contenido")
    artifact = build_bag(
        expediente_id="EXP-1",
        tenant_slug=TENANT_SLUG,
        docs=[doc],
        indice=None,
        generado=datetime.now(tz=UTC),
        actor=None,
    )

    zf = zipfile.ZipFile(io.BytesIO(artifact.zip_bytes))
    tagmanifest = zf.read("tagmanifest-sha256.txt").decode()
    assert "manifest-sha256.txt" in tagmanifest
