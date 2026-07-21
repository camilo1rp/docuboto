# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Ensamblado del ZIP de exportación de expediente (E02/export, storage)."""

import base64
import io
import uuid
import zipfile

import pytest
from pydantic import ValidationError

from tests.conftest import INTERNAL_HEADERS, TENANT_HEADERS, make_file_record, minio_get_object

_URL = "/api/v1/preservacion/export-zip-internal"


def test_export_item_rejects_zip_slip() -> None:
    from app.schemas.preservacion import ExportExtraFile, ExportZipItem

    for bad in ("../../etc/passwd", "a/../../b", "docs/../../x"):
        with pytest.raises(ValidationError):
            ExportZipItem(file_id=uuid.uuid4(), arcname=bad)
    with pytest.raises(ValidationError):
        ExportExtraFile(arcname="../x", contenido_b64="")
    # Ruta relativa con subcarpetas legítimas: válida.
    ok = ExportZipItem(file_id=uuid.uuid4(), arcname="documentos/2026-DEMO-E-1/0-a.pdf")
    assert ok.arcname == "documentos/2026-DEMO-E-1/0-a.pdf"


@pytest.mark.asyncio
async def test_export_zip_endpoint_requires_internal_token(client_with_conn) -> None:
    client, _, _ = client_with_conn
    resp = await client.post(
        _URL, json={"items": [], "extra_files": []}, headers=TENANT_HEADERS
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_export_zip_endpoint_assembles_members(client_with_conn) -> None:
    client, conn, minio = client_with_conn
    fid = uuid.uuid4()
    conn.fetchrow.return_value = make_file_record(file_id=fid, filename="oficio.pdf")
    minio.get_object = minio_get_object(b"PDFBYTES")

    body = {
        "items": [{"file_id": str(fid), "arcname": "documentos/2026-DEMO-E-1/0-oficio.pdf"}],
        "extra_files": [
            {
                "arcname": "manifiesto.csv",
                "contenido_b64": base64.b64encode(b"numero_radicado\n2026-DEMO-E-1").decode(),
            }
        ],
    }
    resp = await client.post(_URL, json=body, headers=INTERNAL_HEADERS)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    # El ensamblado añade checksums.txt (SHA-256 por miembro) automáticamente.
    assert set(zf.namelist()) == {
        "documentos/2026-DEMO-E-1/0-oficio.pdf",
        "manifiesto.csv",
        "checksums.txt",
    }
    assert zf.read("documentos/2026-DEMO-E-1/0-oficio.pdf") == b"PDFBYTES"
    assert b"2026-DEMO-E-1" in zf.read("manifiesto.csv")
    # checksums.txt lleva el SHA-256 real del anexo entregado.
    import hashlib

    digest = hashlib.sha256(b"PDFBYTES").hexdigest()
    checksums = zf.read("checksums.txt").decode()
    assert f"{digest}  documentos/2026-DEMO-E-1/0-oficio.pdf" in checksums
    assert "checksums.txt" not in checksums  # no se incluye a sí mismo


@pytest.mark.asyncio
async def test_export_zip_endpoint_404_when_anexo_missing(client_with_conn) -> None:
    client, conn, _ = client_with_conn
    conn.fetchrow.return_value = None  # get_by_id_any_state → None
    body = {
        "items": [{"file_id": str(uuid.uuid4()), "arcname": "documentos/x/0-a.pdf"}],
        "extra_files": [],
    }
    resp = await client.post(_URL, json=body, headers=INTERNAL_HEADERS)
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "anexo_no_encontrado"
