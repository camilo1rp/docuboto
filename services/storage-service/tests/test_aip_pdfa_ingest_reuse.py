# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Reuso del veredicto PDF/A de INGESTA (E07, `files.pdfa_status`, migración
013) al EMPAQUETAR el AIP (`AipService._reused_pdfa_result`, E10 Increment
C2b follow-up) -- `POST /api/v1/preservacion/aip`.

Mismo patrón/fixtures que `tests/test_aip_pdfa.py`: DB/MinIO mockeados,
`_grant_permission` simula el gate RBAC (min_crud=3). Cubre la matriz
completa del contrato de `_reused_pdfa_result`:
  - veredicto TERMINAL + MISMO perfil -> se REUSA: el validador (JVM/
    subprocess en producción) NUNCA se invoca, y el evento PREMIS queda
    etiquetado `validator_agent="reused/ingest-verdict"`.
  - `pdfa_status` NULL o "pendiente" (no terminal) -> re-valida como si
    nunca se hubiese evaluado en ingesta.
  - veredicto terminal pero para OTRO perfil -> re-valida (el perfil de la
    ingesta y el que exige este AIP no son el mismo compromiso).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

import app.core.config as config_module
import app.services.aip_service as aip_service_module
from tests.conftest import (
    INTERNAL_HEADERS,
    TENANT_SLUG,
    make_file_record,
    make_worm_row,
    minio_get_object,
)

_PDF_CONTENT = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\ncontenido pdf real (reuso ingesta)\n%%EOF"
_PDF_SHA256 = hashlib.sha256(_PDF_CONTENT).hexdigest()


@pytest.fixture(autouse=True)
def _preservation_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module.settings, "preservation_enabled", True)
    monkeypatch.setattr(config_module.settings, "preservation_pdfa_validation_enabled", True)
    # Perfil global por defecto ("2b") -- las pruebas de "mismo perfil" se
    # apoyan en este valor sin necesidad de fijarlo caso a caso.
    monkeypatch.setattr(config_module.settings, "pdfa_profile", "2b")


def _grant_permission(mock_conn) -> None:
    mock_conn.fetchval.return_value = True


def _pdf_record(
    file_id: uuid.UUID,
    *,
    pdfa_status: str | None = None,
    pdfa_profile: str | None = None,
    pdfa_checked_at: datetime | None = None,
) -> dict:
    return make_file_record(
        file_id=file_id,
        filename="oficio.pdf",
        mime_type="application/pdf",
        sha256=_PDF_SHA256,
        object_key=f"{file_id}/oficio.pdf",
        pdfa_status=pdfa_status,
        pdfa_profile=pdfa_profile,
        pdfa_checked_at=pdfa_checked_at,
    )


def _base_payload(*, doc_file_id: uuid.UUID) -> dict:
    return {
        "expediente_id": "EXP-1",
        "documentos": [{"document_id": "R-1", "nombre": "oficio.pdf", "file_id": str(doc_file_id)}],
        "retencion_anios": 5,
        "fecha_inicio": "2026-01-01",
    }


def _aip_insert_row(aip_id: uuid.UUID, retain_until: datetime) -> dict:
    return {
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
    }


def _evento_row(tipo: str) -> dict:
    return {
        "id": uuid.uuid4(),
        "object_ref": "EXP-1",
        "tipo": tipo,
        "resultado": "ok",
        "hash_before": None,
        "hash_after": "c" * 64,
        "detalle": None,
        "agente": None,
        "ts": datetime.now(tz=UTC),
    }


async def _run_empaquetado(client, mock_conn, mock_minio, *, doc_file_id, doc_record):
    aip_id = uuid.uuid4()
    retain_until = datetime(2031, 1, 1, tzinfo=UTC)
    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [
        doc_record,
        None,  # get_aip_by_expediente_and_sha256 (idempotencia: sin duplicado previo)
        _aip_insert_row(aip_id, retain_until),
        make_worm_row(expediente_id="EXP-1", tipo="aip", retain_until=retain_until),
        _evento_row("FIJACION"),
        _evento_row("WORM"),
        _evento_row("INGESTA"),
        _evento_row("VALIDACION_PDFA"),
    ]
    mock_minio.get_object = minio_get_object(_PDF_CONTENT)

    resp = await client.post(
        "/api/v1/preservacion/aip",
        json=_base_payload(doc_file_id=doc_file_id),
        headers=INTERNAL_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    return resp


def _pdfa_evento_detalle(mock_conn) -> dict:
    evento_inserts = [
        c
        for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_evento" in c.args[0] and c.args[2] == "VALIDACION_PDFA"
    ]
    assert len(evento_inserts) == 1
    return json.loads(evento_inserts[0].args[6])


# ---------------------------------------------------------------------------
# Reuso: veredicto TERMINAL + MISMO perfil -> validador NUNCA invocado
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["conformant", "non_conformant", "not_evaluated"])
async def test_reuses_terminal_ingest_verdict_same_profile_never_invokes_validator(
    client_with_conn, monkeypatch: pytest.MonkeyPatch, terminal_status: str
) -> None:
    client, mock_conn, mock_minio = client_with_conn
    doc_file_id = uuid.uuid4()
    record = _pdf_record(
        doc_file_id,
        pdfa_status=terminal_status,
        pdfa_profile="2b",  # == settings.pdfa_profile (default global)
        pdfa_checked_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    spy_validate = AsyncMock()
    monkeypatch.setattr(aip_service_module, "run_pdfa_validation", spy_validate)

    await _run_empaquetado(client, mock_conn, mock_minio, doc_file_id=doc_file_id, doc_record=record)

    spy_validate.assert_not_called()  # el validador caro (JVM/subprocess) NUNCA corrió

    detalle = _pdfa_evento_detalle(mock_conn)
    assert detalle["validator_agent"] == "reused/ingest-verdict"
    assert detalle["outcome"] == terminal_status  # el veredicto de INGESTA, sin re-evaluar
    assert detalle["profile"] == "2b"


# ---------------------------------------------------------------------------
# Sin reuso: no terminal (NULL/"pendiente") o perfil distinto -> re-valida
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pdfa_status_null_never_evaluated_in_ingest_revalidates(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    doc_file_id = uuid.uuid4()
    record = _pdf_record(doc_file_id)  # pdfa_status=None (default)

    # No se parchea `run_pdfa_validation`: debe correr de VERDAD (stub honesto).
    await _run_empaquetado(client, mock_conn, mock_minio, doc_file_id=doc_file_id, doc_record=record)

    detalle = _pdfa_evento_detalle(mock_conn)
    assert detalle["validator_agent"] == "stub/validator_unavailable"  # SE re-evaluó
    assert detalle["outcome"] == "not_evaluated"


@pytest.mark.asyncio
async def test_pdfa_status_pendiente_not_terminal_revalidates(client_with_conn) -> None:
    """`pendiente` (worker asíncrono de ingesta encolado pero aún sin
    correr) NO es un estado terminal -- el AIP no puede esperar a ese
    worker, así que re-valida como si no hubiera nada registrado."""
    client, mock_conn, mock_minio = client_with_conn
    doc_file_id = uuid.uuid4()
    record = _pdf_record(doc_file_id, pdfa_status="pendiente", pdfa_profile="2b")

    await _run_empaquetado(client, mock_conn, mock_minio, doc_file_id=doc_file_id, doc_record=record)

    detalle = _pdfa_evento_detalle(mock_conn)
    assert detalle["validator_agent"] == "stub/validator_unavailable"
    assert detalle["outcome"] == "not_evaluated"


@pytest.mark.asyncio
async def test_pdfa_status_terminal_different_profile_revalidates(client_with_conn) -> None:
    """Veredicto terminal de ingesta, pero para un perfil DISTINTO al que
    este AIP exige (p. ej. la serie TRD del expediente pide "1b" mientras
    que la ingesta validó contra el "2b" global) -- no es el MISMO
    compromiso de conformidad, así que se re-valida."""
    client, mock_conn, mock_minio = client_with_conn
    doc_file_id = uuid.uuid4()
    record = _pdf_record(
        doc_file_id,
        pdfa_status="conformant",
        pdfa_profile="1b",  # != settings.pdfa_profile ("2b")
        pdfa_checked_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    await _run_empaquetado(client, mock_conn, mock_minio, doc_file_id=doc_file_id, doc_record=record)

    detalle = _pdfa_evento_detalle(mock_conn)
    assert detalle["validator_agent"] == "stub/validator_unavailable"  # re-evaluado, no reusado
    assert detalle["outcome"] == "not_evaluated"
    assert detalle["profile"] == "2b"  # se validó contra el perfil que EXIGE este AIP
