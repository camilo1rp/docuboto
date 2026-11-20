# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Ensamblado del ZIP de exportación de expediente (E02/export) por `storage.api.open_export_zip`."""

import base64
import hashlib
import io
import shutil
import uuid
import zipfile
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

import storage.core.config as cfg
from storage import api as storage_api
from tests.storage.conftest import TENANT_SLUG, make_file_record, minio_get_object_stream


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


async def _export(body: dict) -> bytes:
    """El ZIP completo, leído por el mismo iterador que sirve archive."""
    stream = await storage_api.open_export_zip(tenant_slug=TENANT_SLUG, **body)
    contenido = b"".join([trozo async for trozo in stream.iter_bytes()])
    assert stream.content_length == len(contenido)
    return contenido


async def _export_error(body: dict) -> storage_api.StorageExportError:
    with pytest.raises(storage_api.StorageExportError) as exc:
        await storage_api.open_export_zip(tenant_slug=TENANT_SLUG, **body)
    return exc.value


def test_export_item_rejects_zip_slip() -> None:
    from storage.schemas.preservacion import ExportExtraFile, ExportZipItem

    for bad in ("../../etc/passwd", "a/../../b", "docs/../../x"):
        with pytest.raises(ValidationError):
            ExportZipItem(file_id=uuid.uuid4(), arcname=bad)
    with pytest.raises(ValidationError):
        ExportExtraFile(arcname="../x", contenido_b64="")
    # Ruta relativa con subcarpetas legítimas: válida.
    ok = ExportZipItem(file_id=uuid.uuid4(), arcname="documentos/2026-DEMO-E-1/0-a.pdf")
    assert ok.arcname == "documentos/2026-DEMO-E-1/0-a.pdf"


@pytest.mark.asyncio
async def test_export_zip_assembles_members(client_with_conn) -> None:
    _client, conn, minio = client_with_conn
    fid = uuid.uuid4()
    conn.fetch.return_value = [
        make_file_record(file_id=fid, filename="oficio.pdf", sha256=_sha(b"PDFBYTES"))
    ]
    minio.get_object = minio_get_object_stream(b"PDFBYTES")

    body = {
        "items": [{"file_id": str(fid), "arcname": "documentos/2026-DEMO-E-1/0-oficio.pdf"}],
        "extra_files": [
            {
                "arcname": "manifiesto.csv",
                "contenido_b64": base64.b64encode(b"numero_radicado\n2026-DEMO-E-1").decode(),
            }
        ],
    }
    zf = zipfile.ZipFile(io.BytesIO(await _export(body)))
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
async def test_export_zip_404_when_anexo_missing(client_with_conn, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cfg.settings, 'export_tmp_dir', str(tmp_path))
    _client, conn, minio = client_with_conn
    conn.fetch.return_value = []  # ningún id existe
    body = {
        "items": [{"file_id": str(uuid.uuid4()), "arcname": "documentos/x/0-a.pdf"}],
        "extra_files": [],
    }
    assert (await _export_error(body)).status_code == 404
    # Sin temporal: la prevalidación falla antes de crear el fichero y no se
    # tocó el almacén de objetos.
    assert list(tmp_path.iterdir()) == []
    minio.get_object.assert_not_called()


def _ids_body(fid: uuid.UUID) -> dict:
    return {"items": [{"file_id": str(fid), "arcname": "documentos/x/0-a.pdf"}], "extra_files": []}


@pytest.mark.asyncio
async def test_export_deletes_temp_after_serving(client_with_conn, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cfg.settings, "export_tmp_dir", str(tmp_path))
    _client, conn, minio = client_with_conn
    fid = uuid.uuid4()
    conn.fetch.return_value = [make_file_record(file_id=fid, sha256=_sha(b"X" * 5000))]
    minio.get_object = minio_get_object_stream(b"X" * 5000)
    contenido = await _export(_ids_body(fid))
    assert zipfile.ZipFile(io.BytesIO(contenido)).testzip() is None
    assert list(tmp_path.iterdir()) == []  # el temporal se borra tras servir


@pytest.mark.asyncio
async def test_export_failure_midway_leaves_no_temp_and_no_200(
    client_with_conn, monkeypatch, tmp_path
) -> None:
    """Si el almacén falla a mitad de un anexo, el error llega ANTES de servir
    y el temporal parcial se borra (nunca un 200 truncado)."""
    monkeypatch.setattr(cfg.settings, "export_tmp_dir", str(tmp_path))
    _client, conn, minio = client_with_conn
    fid = uuid.uuid4()
    conn.fetch.return_value = [make_file_record(file_id=fid, sha256=_sha(b"parcial"))]

    async def _boom(_n: int):
        yield b"parcial"
        raise RuntimeError("corte del almacén")

    obj = MagicMock()
    obj.content.iter_chunked = MagicMock(side_effect=_boom)
    minio.get_object = AsyncMock(return_value=obj)
    exc = await _export_error(_ids_body(fid))
    assert isinstance(exc.__cause__, RuntimeError)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_export_413_when_declared_size_exceeds_cap(
    client_with_conn, monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(cfg.settings, "export_tmp_dir", str(tmp_path))
    monkeypatch.setattr(cfg.settings, "export_max_bytes", 1000)
    _client, conn, minio = client_with_conn
    fid = uuid.uuid4()
    conn.fetch.return_value = [make_file_record(file_id=fid, file_size=5000)]
    assert (await _export_error(_ids_body(fid))).status_code == 413
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_export_413_when_real_bytes_exceed_cap_and_cleans(
    client_with_conn, monkeypatch, tmp_path
) -> None:
    """El tamaño declarado miente (cabe) pero el real no: corta y limpia."""
    monkeypatch.setattr(cfg.settings, "export_tmp_dir", str(tmp_path))
    monkeypatch.setattr(cfg.settings, "export_max_bytes", 1000)
    _client, conn, minio = client_with_conn
    fid = uuid.uuid4()
    conn.fetch.return_value = [make_file_record(file_id=fid, file_size=10)]
    minio.get_object = minio_get_object_stream(b"Z" * 5000)
    assert (await _export_error(_ids_body(fid))).status_code == 413
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_export_507_when_disk_insufficient(client_with_conn, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cfg.settings, "export_tmp_dir", str(tmp_path))
    monkeypatch.setattr(
        "storage.services.export_service.shutil.disk_usage",
        lambda _p: shutil._ntuple_diskusage(100, 99, 1),
    )
    _client, conn, _ = client_with_conn
    fid = uuid.uuid4()
    conn.fetch.return_value = [make_file_record(file_id=fid)]
    assert (await _export_error(_ids_body(fid))).status_code == 507
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_export_peak_memory_is_bounded_for_large_attachment(
    client_with_conn, monkeypatch, tmp_path
) -> None:
    """600 MB perezosos: el pico de memoria NO crece con el anexo (se escribe
    por trozos a disco). Falla si se vuelve a bufferizar (`await obj.read()`)."""
    import tracemalloc

    monkeypatch.setattr(cfg.settings, "export_tmp_dir", str(tmp_path))
    monkeypatch.setattr(cfg.settings, "export_min_free_bytes", 0)
    monkeypatch.setattr(
        "storage.services.export_service.shutil.disk_usage",
        lambda _p: shutil._ntuple_diskusage(10**12, 0, 10**12),
    )
    _client, conn, minio = client_with_conn
    fid = uuid.uuid4()
    total = 600 * 1024 * 1024
    trozo = b"\0" * (1024 * 1024)
    h = hashlib.sha256()
    for _ in range(total // len(trozo)):
        h.update(trozo)
    conn.fetch.return_value = [
        make_file_record(file_id=fid, file_size=total, sha256=h.hexdigest())
    ]

    async def _lazy(_n: int):
        for _ in range(total // len(trozo)):
            yield trozo

    obj = MagicMock()
    obj.content.iter_chunked = MagicMock(side_effect=_lazy)
    # `.read()` existe pero materializaría 600 MB: si alguien lo usa, el pico lo delata.
    obj.read = AsyncMock(side_effect=lambda: b"\0" * total)
    minio.get_object = AsyncMock(return_value=obj)

    tracemalloc.start()
    try:
        stream = await storage_api.open_export_zip(tenant_slug=TENANT_SLUG, **_ids_body(fid))
        _, pico = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    await stream.aclose()
    assert pico < 64 * 1024 * 1024
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_export_404_when_object_missing_in_store(
    client_with_conn, monkeypatch, tmp_path
) -> None:
    """Fila presente, binario ausente: 404 limpio y sin temporal."""
    from miniopy_async.error import S3Error

    monkeypatch.setattr(cfg.settings, "export_tmp_dir", str(tmp_path))
    _client, conn, minio = client_with_conn
    fid = uuid.uuid4()
    conn.fetch.return_value = [make_file_record(file_id=fid)]
    minio.get_object = AsyncMock(
        side_effect=S3Error("NoSuchKey", "no existe", "r", "rid", "hid", MagicMock())
    )
    assert (await _export_error(_ids_body(fid))).status_code == 404
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_export_409_fixity_mismatch_aborts_before_serving_and_audits(
    client_with_conn, monkeypatch, tmp_path
) -> None:
    """B4 media: el binario difiere del SHA-256 registrado -> error, sin ZIP,
    sin temporal y con asiento de incidente (nunca `checksums.txt` sobre bytes
    corruptos)."""
    monkeypatch.setattr(cfg.settings, "export_tmp_dir", str(tmp_path))
    registrados = []

    async def _append(_conn, **kw):
        registrados.append(kw)

    monkeypatch.setattr("storage.services.export_service.audit.append", _append)
    _client, conn, minio = client_with_conn
    fid = uuid.uuid4()
    conn.fetch.return_value = [make_file_record(file_id=fid, sha256=_sha(b"ORIGINAL"))]
    minio.get_object = minio_get_object_stream(b"CORRUPTO")
    assert (await _export_error(_ids_body(fid))).status_code == 409
    assert list(tmp_path.iterdir()) == []
    assert len(registrados) == 1
    assert registrados[0]["action"] == "file.fixity_fallo"
    assert registrados[0]["object_ref"] == str(fid)
    assert registrados[0]["payload"]["hash_obtenido"] == _sha(b"CORRUPTO")


@pytest.mark.asyncio
async def test_en_hilo_espera_al_hilo_si_cancelan() -> None:
    """B5 baja: cancelar durante la escritura NO vuelve mientras el hilo escribe."""
    import asyncio
    import threading
    import time

    from storage.services.export_service import _en_hilo

    terminado = threading.Event()

    def _lento() -> None:
        time.sleep(0.3)
        terminado.set()

    tarea = asyncio.ensure_future(_en_hilo(_lento))
    await asyncio.sleep(0.05)
    tarea.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tarea
    assert terminado.is_set()
