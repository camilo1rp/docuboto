# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de la exportación interoperable (E11, RF-INT-01) — nivel ExportService.

La lógica del paquete (validación contra el esquema publicado, estructura del ZIP,
manifiesto con UUID/comentario/marcas/excluidos, checksums, bytes de anexos con
SHA-256) se prueba aquí con un `ExportRepository` mockeado. El **no-read-up SQL** del
repositorio (excluir sobre-clearance + contar excluidos) se valida contra Postgres
real en el smoke `export_smoke.py`."""

import hashlib
import io
import json
import uuid
import zipfile
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from jsonschema import Draft202012Validator

from app.services.export_service import (
    MAX_EXPORT_RADICADOS,
    RADICADO_SCHEMA,
    ExportService,
    ExportTooLargeError,
)


@pytest.fixture(autouse=True)
def _no_audit():
    """El asiento de egreso (audit.append) se prueba en orpycamcp_common; aquí se
    neutraliza para no ejercer el hash-chain sobre un conn nulo."""
    with patch("app.services.export_service.audit.append", new=AsyncMock(return_value="h")):
        yield


def _row(tn="2026-DEMO-E-000001", nivel=1, con_anexo=False, antecedente=None):
    return {
        "id": uuid.uuid4(),
        "antecedente": antecedente,
        "tracking_number": tn,
        "doc_type": "E",
        "year": 2026,
        "sequence": 1,
        "subject": "Acta pública",
        "doc_class": "OFICIO",
        "origin_dept": "Secretaría",
        "dest_dept": None,
        "sender_name": "Juan",
        "sender_entity": None,
        "status": "registered",
        "pages": 2,
        "observations": None,
        "registered_at": datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
        "updated_at": datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
        "tenant_code": "DEMO",
        "nivel_seguridad": nivel,
        "metadata": {"prioridad": "alta"},
        "disposition": None,
        "clasificacion_fundamento": None,
        "clasificacion_causal": None,
        "clasificacion_plazo_meses": None,
        "clasificado_at": None,
        "_con_anexo": con_anexo,
    }


def _mock_repo(rows, *, excluidos=0, incluidos=None):
    repo = AsyncMock()
    repo.list_radicados.return_value = rows
    repo.count_incluidos.return_value = len(rows) if incluidos is None else incluidos
    repo.count_excluidos.return_value = excluidos

    async def _anexos(radicado_id):
        # devuelve un anexo solo para las filas marcadas
        return [
            {"file_id": uuid.uuid4(), "filename": "a.pdf", "file_size": 5, "mime_type": "application/pdf"}
        ] if any(r["id"] == radicado_id and r["_con_anexo"] for r in rows) else []

    repo.list_anexos.side_effect = _anexos
    repo.history.return_value = [
        {"action": "document.radicado.creado", "ts": datetime(2026, 3, 1, tzinfo=UTC),
         "actor": "u1", "canal": "api"}
    ]
    return repo


def _svc(rows, *, excluidos=0, incluidos=None):
    svc = ExportService(conn=None, tenant_slug="demo")
    svc._repo = _mock_repo(rows, excluidos=excluidos, incluidos=incluidos)
    return svc


@pytest.mark.asyncio
async def test_package_structure_and_manifest() -> None:
    svc = _svc([_row(), _row(tn="2026-DEMO-S-000002")], excluidos=3)
    zbytes, export_uuid, num, excl = await svc.build_export(
        max_clearance=1, comentario="entrega trimestral", year=None, doc_type=None
    )
    assert num == 2 and excl == 3
    zf = zipfile.ZipFile(io.BytesIO(zbytes))
    names = set(zf.namelist())
    assert "manifiesto.json" in names
    assert "esquema/radicado.schema.json" in names
    assert "LEEME.txt" in names
    assert "checksums.txt" in names
    assert "radicados/2026-DEMO-E-000001.json" in names

    man = json.loads(zf.read("manifiesto.json"))
    assert man["export_uuid"] == export_uuid
    assert man["comentario"] == "entrega trimestral"
    assert man["generado_inicio"] and man["generado_fin"]
    assert man["num_radicados"] == 2
    assert man["excluidos"]["num"] == 3  # sobre-clearance reportado content-free
    assert len(man["radicados"]) == 2


@pytest.mark.asyncio
async def test_each_radicado_validates_against_published_schema() -> None:
    svc = _svc([_row()])
    zbytes, *_ = await svc.build_export(max_clearance=1, comentario=None, year=None, doc_type=None)
    zf = zipfile.ZipFile(io.BytesIO(zbytes))
    schema = json.loads(zf.read("esquema/radicado.schema.json"))
    validator = Draft202012Validator(schema)
    entity = json.loads(zf.read("radicados/2026-DEMO-E-000001.json"))
    validator.validate(entity)  # el esquema DEL paquete valida el dato DEL paquete
    assert entity["numero_radicado"] == "2026-DEMO-E-000001"  # identidad preservada
    assert entity["metadata_contextual"] == {"prioridad": "alta"}
    assert entity["historial"][0]["accion"] == "document.radicado.creado"


@pytest.mark.asyncio
async def test_checksums_match_members() -> None:
    svc = _svc([_row()])
    zbytes, *_ = await svc.build_export(max_clearance=1, comentario=None, year=None, doc_type=None)
    zf = zipfile.ZipFile(io.BytesIO(zbytes))
    checks = {}
    for line in zf.read("checksums.txt").decode().splitlines():
        h, name = line.split("  ", 1)
        checks[name] = h
    for name, h in checks.items():
        assert hashlib.sha256(zf.read(name)).hexdigest() == h


@pytest.mark.asyncio
async def test_anexo_bytes_included_with_sha256() -> None:
    svc = _svc([_row(con_anexo=True)])

    class _FakeStream:
        async def aiter_bytes(self):
            yield b"PDFDATA"

        async def aclose(self):
            return None

    with patch(
        "app.services.export_service.storage_client.open_content_stream",
        new=AsyncMock(return_value=_FakeStream()),
    ):
        zbytes, *_ = await svc.build_export(
            max_clearance=1, comentario=None, year=None, doc_type=None
        )
    zf = zipfile.ZipFile(io.BytesIO(zbytes))
    entity = json.loads(zf.read("radicados/2026-DEMO-E-000001.json"))
    assert len(entity["anexos"]) == 1
    anexo = entity["anexos"][0]
    assert anexo["sha256"] == hashlib.sha256(b"PDFDATA").hexdigest()
    # el binario está en el paquete en la ruta declarada
    assert zf.read(anexo["archivo"]) == b"PDFDATA"


@pytest.mark.asyncio
async def test_purged_anexo_is_traceable_not_fatal() -> None:
    """Un anexo purgado en storage (stream None) no aborta el export y queda como
    entrada `ausente` TRAZABLE (no se descarta en silencio)."""
    svc = _svc([_row(con_anexo=True)])
    with patch(
        "app.services.export_service.storage_client.open_content_stream",
        new=AsyncMock(return_value=None),
    ):
        zbytes, _u, num, _e = await svc.build_export(
            max_clearance=1, comentario=None, year=None, doc_type=None
        )
    assert num == 1
    zf = zipfile.ZipFile(io.BytesIO(zbytes))
    entity = json.loads(zf.read("radicados/2026-DEMO-E-000001.json"))
    assert len(entity["anexos"]) == 1
    assert entity["anexos"][0]["ausente"] is True
    assert "archivo" not in entity["anexos"][0]  # no apunta a un miembro inexistente


@pytest.mark.asyncio
async def test_antecedente_exported_as_portable_number() -> None:
    """La relación E↔S (`responde_a`) se exporta como NÚMERO de radicado (identidad
    portable), no como UUID interno — totalidad referencial (RN-3)."""
    svc = _svc([_row(tn="2026-DEMO-S-000005", antecedente="2026-DEMO-E-000001")])
    zbytes, *_ = await svc.build_export(max_clearance=1, comentario=None, year=None, doc_type=None)
    zf = zipfile.ZipFile(io.BytesIO(zbytes))
    entity = json.loads(zf.read("radicados/2026-DEMO-S-000005.json"))
    assert entity["antecedente"] == "2026-DEMO-E-000001"


@pytest.mark.asyncio
async def test_export_too_large_raises() -> None:
    """Tope anti-DoS: si el conteo excede MAX, aborta antes de armar el ZIP."""
    svc = _svc([_row()], incluidos=MAX_EXPORT_RADICADOS + 1)
    with pytest.raises(ExportTooLargeError):
        await svc.build_export(max_clearance=1, comentario=None, year=None, doc_type=None)


def test_schema_is_valid_jsonschema() -> None:
    """El esquema publicado es un JSON Schema draft 2020-12 válido."""
    Draft202012Validator.check_schema(RADICADO_SCHEMA)
