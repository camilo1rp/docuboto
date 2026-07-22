# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de la importación interoperable (E11, RF-INT-05) — nivel ImportService.

La validación (fixity + esquema + zip-slip/zip-bomb) se prueba con paquetes
crafteados (sin BD); el ingest con repo/storage mockeados. El **round-trip
export→import** contra Postgres real vive en `import_roundtrip_smoke.py`."""

import hashlib
import io
import json
import uuid
import zipfile
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.export_service import RADICADO_SCHEMA
from app.services.import_service import PackageValidationError, ImportService


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _pkg(entities, *, tamper_binary=False, tamper_schema=False, bad_member=False,
         anexo_no_archivo=False) -> bytes:
    """Craftea un paquete estilo INT-01. `entities` = lista de dicts
    {tracking, nivel?, antecedente?, anexos?=[(filename, bytes)]}."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("esquema/radicado.schema.json", json.dumps(RADICADO_SCHEMA).encode())
        manifest_rads = []
        for e in entities:
            tn = e["tracking"]
            anexos_json = []
            for i, (fn, data) in enumerate(e.get("anexos", [])):
                if anexo_no_archivo:
                    # anexo no-ausente pero SIN archivo/sha256 (paquete malformado)
                    anexos_json.append({"nombre_original": fn})
                    continue
                arc = f"anexos/{tn}/{i}-{fn}"
                zf.writestr(arc, b"TAMPERED" if tamper_binary else data)
                anexos_json.append({
                    "nombre_original": fn, "archivo": arc, "sha256": _sha(data),
                    "mime_type": "application/pdf", "tamano_bytes": len(data),
                })
            entity = {
                "tipo_entidad": "radicado", "numero_radicado": tn, "tipo": "E",
                "asunto": "Acta", "nivel_seguridad": e.get("nivel", 1),
                "fecha_radicacion": "2026-03-01T10:00:00Z",
                "metadata_sistema": {"status": "registered", "pages": 1},
                "metadata_contextual": {"k": "v"}, "disposicion": None,
                "clasificacion": None, "antecedente": e.get("antecedente"),
                "anexos": anexos_json, "historial": [],
            }
            if tamper_schema:
                del entity["asunto"]  # falta un required
            body = json.dumps(entity).encode()
            data_file = f"radicados/{tn}.json"
            zf.writestr(data_file, body)
            manifest_rads.append({"numero_radicado": tn, "archivo": data_file, "sha256": _sha(body)})
        if bad_member:
            zf.writestr("../evil.txt", b"x")
        zf.writestr("manifiesto.json", json.dumps({"export_uuid": "u", "radicados": manifest_rads}).encode())
    return buf.getvalue()


@asynccontextmanager
async def _noop_tx():
    yield


def _svc():
    conn = AsyncMock()
    conn.transaction = MagicMock(side_effect=lambda *a, **k: _noop_tx())
    svc = ImportService(conn, tenant_slug="demo")
    svc._repo = AsyncMock()
    svc._repo.exists.return_value = False
    svc._repo.insert_radicado.return_value = uuid.uuid4()
    svc._repo.id_by_tracking.return_value = None
    return svc


async def _run(svc, zip_bytes, *, clearance=1, actor=None):
    with patch(
        "app.services.import_service.storage_client.upload_bytes",
        new=AsyncMock(return_value={"file_id": str(uuid.uuid4()), "sha256": "x"}),
    ), patch("app.services.import_service.audit.append", new=AsyncMock(return_value="h")):
        return await svc.import_package(zip_bytes=zip_bytes, max_clearance=clearance, actor=actor)


# --- validación (todo-o-nada, sin BD) ----------------------------------------


@pytest.mark.asyncio
async def test_valid_package_ingests() -> None:
    svc = _svc()
    pkg = _pkg([{"tracking": "2026-DEMO-E-000001", "anexos": [("a.pdf", b"PDFBYTES")]}])
    report = await _run(svc, pkg)
    assert report["importados"] == 1
    assert report["omitidos_existentes"] == 0
    svc._repo.insert_radicado.assert_awaited()
    svc._repo.insert_anexo.assert_awaited()


@pytest.mark.asyncio
async def test_binary_checksum_mismatch_aborts() -> None:
    """RF-INT-05 (crítico): un binario cuyo SHA-256 no coincide aborta ANTES de BD."""
    svc = _svc()
    pkg = _pkg([{"tracking": "2026-DEMO-E-000001", "anexos": [("a.pdf", b"REAL")]}], tamper_binary=True)
    with pytest.raises(PackageValidationError, match="checksum del binario"):
        await _run(svc, pkg)
    svc._repo.insert_radicado.assert_not_awaited()  # nada se ingirió


@pytest.mark.asyncio
async def test_schema_violation_aborts() -> None:
    svc = _svc()
    pkg = _pkg([{"tracking": "2026-DEMO-E-000001"}], tamper_schema=True)
    with pytest.raises(PackageValidationError, match="no valida contra el esquema"):
        await _run(svc, pkg)
    svc._repo.insert_radicado.assert_not_awaited()


@pytest.mark.asyncio
async def test_zip_slip_member_rejected() -> None:
    svc = _svc()
    pkg = _pkg([{"tracking": "2026-DEMO-E-000001"}], bad_member=True)
    with pytest.raises(PackageValidationError, match="no permitida"):
        await _run(svc, pkg)


@pytest.mark.asyncio
async def test_not_a_zip_rejected() -> None:
    svc = _svc()
    with pytest.raises(PackageValidationError, match="ZIP válido"):
        await _run(svc, b"esto no es un zip")


# --- políticas de ingest ------------------------------------------------------


@pytest.mark.asyncio
async def test_no_write_up_skips_over_clearance() -> None:
    """No-write-up: un radicado clasificado no se ingiere si el importador es PUBLICA."""
    svc = _svc()
    pkg = _pkg([{"tracking": "2026-DEMO-E-000001", "nivel": 3}])
    report = await _run(svc, pkg, clearance=1)
    assert report["importados"] == 0
    assert report["omitidos_clearance"] == 1
    svc._repo.insert_radicado.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_tracking_skipped_not_overwritten() -> None:
    """Identidad inmutable: una colisión de número se OMITE (no se sobreescribe)."""
    svc = _svc()
    svc._repo.exists.return_value = True
    pkg = _pkg([{"tracking": "2026-DEMO-E-000001"}])
    report = await _run(svc, pkg)
    assert report["importados"] == 0
    assert report["omitidos_existentes"] == 1
    svc._repo.insert_radicado.assert_not_awaited()


@pytest.mark.asyncio
async def test_antecedente_relation_reconstructed() -> None:
    """La relación E↔S se reconstruye por NÚMERO tras insertar (id_by_tracking)."""
    svc = _svc()
    ant_id = uuid.uuid4()
    svc._repo.id_by_tracking.return_value = ant_id
    pkg = _pkg([{"tracking": "2026-DEMO-S-000002", "antecedente": "2026-DEMO-E-000001"}])
    report = await _run(svc, pkg)
    assert report["importados"] == 1
    assert report["relaciones_reconstruidas"] == 1
    assert report["antecedentes_no_resueltos"] == []
    svc._repo.set_responde_a.assert_awaited()


@pytest.mark.asyncio
async def test_unresolved_antecedente_is_declared() -> None:
    """#4: un antecedente que no se encuentra NO se descarta en silencio — se DECLARA."""
    svc = _svc()
    svc._repo.id_by_tracking.return_value = None  # el antecedente no está
    pkg = _pkg([{"tracking": "2026-DEMO-S-000002", "antecedente": "2026-DEMO-E-999999"}])
    report = await _run(svc, pkg)
    assert report["importados"] == 1
    assert report["relaciones_reconstruidas"] == 0
    assert report["antecedentes_no_resueltos"] == ["2026-DEMO-E-999999"]
    svc._repo.set_responde_a.assert_not_awaited()


@pytest.mark.asyncio
async def test_anexo_without_archivo_rejected() -> None:
    """#5: un anexo no-ausente sin archivo/sha256 → 422 (no un 500)."""
    svc = _svc()
    pkg = _pkg([{"tracking": "2026-DEMO-E-000001", "anexos": [("a.pdf", b"X")]}], anexo_no_archivo=True)
    with pytest.raises(PackageValidationError, match="sin archivo/sha256"):
        await _run(svc, pkg)
    svc._repo.insert_radicado.assert_not_awaited()
