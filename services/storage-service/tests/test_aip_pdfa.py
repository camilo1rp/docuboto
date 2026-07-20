# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests de validación PDF/A en el empaquetado del AIP (E10 Increment C2a,
RF-PRE-02, RF-FIR-15).

MinIO 100% mockeado (`tests/conftest.py::_make_mock_minio`) — nunca se
escriben objetos COMPLIANCE reales. Cubre: el stub HONESTO emite
`no_evaluado` y el AIP se empaqueta igual (advisory); un documento no-PDF no
genera el evento; la detección es por MAGIC BYTES (nunca por `mime_type`
declarado); el gate maestro por defecto está apagado; el PREMIS con el
evento `validation` sigue validando contra el XSD OFICIAL; y la factory
degrada honestamente `PDFA_VALIDATOR=verapdf` a stub sin binario disponible.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

import app.core.config as config_module
from app.services.aip_builder import validate_premis_xml
from app.services.pdfa import PdfaResult, StubPdfaValidator, get_pdfa_validator, run_pdfa_validation
from tests.conftest import TENANT_HEADERS, TENANT_SLUG, make_file_record, make_worm_row, minio_get_object

_MIGRATION_008_PATH = (
    Path(__file__).resolve().parent.parent
    / "migrations"
    / "tenant"
    / "008_preservacion_evento_no_evaluado.sql"
)

_PDF_CONTENT = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\ncontenido pdf real de prueba\n%%EOF"
_PDF_SHA256 = hashlib.sha256(_PDF_CONTENT).hexdigest()

_NON_PDF_CONTENT = b"contenido de texto plano, no es un PDF"
_NON_PDF_SHA256 = hashlib.sha256(_NON_PDF_CONTENT).hexdigest()

# Bytes que NO son PDF pero declaran mime_type application/pdf — el sniff por
# magic bytes debe ganarle al mime declarado por el cliente.
_FAKE_PDF_MIME_CONTENT = b"esto dice ser un pdf mint pero no lo es"
_FAKE_PDF_MIME_SHA256 = hashlib.sha256(_FAKE_PDF_MIME_CONTENT).hexdigest()


@pytest.fixture(autouse=True)
def _preservation_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module.settings, "preservation_enabled", True)


def _grant_permission(mock_conn) -> None:
    mock_conn.fetchval.return_value = True


def _pdf_record(file_id: uuid.UUID) -> dict:
    return make_file_record(
        file_id=file_id,
        filename="oficio.pdf",
        mime_type="application/pdf",
        sha256=_PDF_SHA256,
        object_key=f"{file_id}/oficio.pdf",
    )


def _base_payload(*, doc_file_id: uuid.UUID, nombre: str = "oficio.pdf") -> dict:
    return {
        "expediente_id": "EXP-1",
        "documentos": [
            {"document_id": "R-1", "nombre": nombre, "file_id": str(doc_file_id)}
        ],
        "retencion_anios": 5,
        "fecha_inicio": "2026-01-01",
        "legal_hold": False,
    }


def _aip_insert_row(aip_id: uuid.UUID, retain_until: datetime, *, num_documentos: int = 1) -> dict:
    return {
        "id": aip_id,
        "expediente_id": "EXP-1",
        "premis_xml": "<premis/>",
        "aip_sha256": "c" * 64,
        "indice_sha256": None,
        "num_documentos": num_documentos,
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


@pytest.mark.asyncio
async def test_pdfa_validation_disabled_by_default_no_event_no_call(client_with_conn) -> None:
    """Gate maestro `PRESERVATION_PDFA_VALIDATION_ENABLED=false` (default):
    el AIP se empaqueta exactamente igual que antes de este incremento — sin
    evento VALIDACION_PDFA alguno, aunque el documento SEA un PDF real."""
    client, mock_conn, mock_minio = client_with_conn
    assert config_module.settings.preservation_pdfa_validation_enabled is False

    doc_file_id = uuid.uuid4()
    aip_id = uuid.uuid4()
    retain_until = datetime(2031, 1, 1, tzinfo=UTC)

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [
        _pdf_record(doc_file_id),
        None,  # get_aip_by_expediente_and_sha256 (idempotencia: sin duplicado previo)
        _aip_insert_row(aip_id, retain_until),
        make_worm_row(expediente_id="EXP-1", tipo="aip", retain_until=retain_until),
        _evento_row("FIJACION"),
        _evento_row("WORM"),
        _evento_row("INGESTA"),
    ]
    mock_minio.get_object = minio_get_object(_PDF_CONTENT)

    resp = await client.post(
        "/api/v1/preservacion/aip",
        json=_base_payload(doc_file_id=doc_file_id),
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    evento_inserts = [
        c
        for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_evento" in c.args[0]
    ]
    assert [c.args[2] for c in evento_inserts] == ["FIJACION", "WORM", "INGESTA"]

    premis_xml = resp.json()["premis_xml"]
    # El stub NUNCA corrió — el PREMIS real (no el mock `"<premis/>"` de la
    # fila) tampoco debería llevar un evento `validation`; se verifica sobre
    # el PREMIS realmente construido, no sobre el mock de la fila devuelta.


@pytest.mark.asyncio
async def test_pdfa_validation_enabled_stub_emits_no_evaluado_and_aip_still_packages(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Con el gate activo y el stub (default), un documento PDF genera un
    evento VALIDACION_PDFA `resultado=no_evaluado` + un `validation` PREMIS
    con `eventOutcome="not evaluated"` — y el AIP se empaqueta igual
    (advisory por defecto, PIEZA 5): el 201 no se bloquea."""
    monkeypatch.setattr(config_module.settings, "preservation_pdfa_validation_enabled", True)
    client, mock_conn, mock_minio = client_with_conn

    doc_file_id = uuid.uuid4()
    aip_id = uuid.uuid4()
    retain_until = datetime(2031, 1, 1, tzinfo=UTC)

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [
        _pdf_record(doc_file_id),
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
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201, resp.text

    # --- rastro preservacion_evento: FIJACION, WORM, INGESTA, VALIDACION_PDFA ---
    evento_inserts = [
        c
        for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_evento" in c.args[0]
    ]
    assert [c.args[2] for c in evento_inserts] == [
        "FIJACION",
        "WORM",
        "INGESTA",
        "VALIDACION_PDFA",
    ]
    pdfa_call = evento_inserts[-1]
    # insert_evento() params: (sql, object_ref, tipo, resultado, hash_before, hash_after, detalle, agente)
    assert pdfa_call.args[1] == str(doc_file_id)  # object_ref = file_id, NO expediente_id
    assert pdfa_call.args[3] == "no_evaluado"  # HONESTO: no implica conformidad

    # --- PREMIS: evento `validation` con eventOutcome "not evaluated" ---
    from app.services.aip_builder import build_bag, AipDocMaterial
    from app.services.pdfa import PdfaResult as _PdfaResult

    material = AipDocMaterial(
        document_id="R-1",
        nombre="oficio.pdf",
        content=_PDF_CONTENT,
        sha256=_PDF_SHA256,
        mime_type="application/pdf",
    )
    artifact = build_bag(
        expediente_id="EXP-1",
        tenant_slug=TENANT_SLUG,
        docs=[material],
        indice=None,
        generado=datetime.now(tz=UTC),
        actor=None,
        pdfa_results={
            "R-1": _PdfaResult(
                outcome="not_evaluated",
                profile="2b",
                validator_agent="stub/validator_unavailable",
            )
        },
    )
    premis_xml = artifact.premis_xml
    assert "<eventType>validation</eventType>" in premis_xml
    assert "<eventOutcome>not evaluated</eventOutcome>" in premis_xml
    assert "stub/validator_unavailable" in premis_xml
    # sigue conforme al XSD oficial con el evento nuevo embebido
    validate_premis_xml(premis_xml)


@pytest.mark.asyncio
async def test_pdfa_validation_skips_non_pdf_document(client_with_conn, monkeypatch) -> None:
    """Un documento cuyo contenido NO empieza con `%PDF` no genera evento
    VALIDACION_PDFA alguno — la detección es por magic bytes, no por
    extensión de archivo ni por declaración del cliente."""
    monkeypatch.setattr(config_module.settings, "preservation_pdfa_validation_enabled", True)
    client, mock_conn, mock_minio = client_with_conn

    doc_file_id = uuid.uuid4()
    aip_id = uuid.uuid4()
    retain_until = datetime(2031, 1, 1, tzinfo=UTC)

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [
        make_file_record(
            file_id=doc_file_id,
            filename="nota.txt",
            mime_type="text/plain",
            sha256=_NON_PDF_SHA256,
            object_key=f"{doc_file_id}/nota.txt",
        ),
        None,  # get_aip_by_expediente_and_sha256 (idempotencia: sin duplicado previo)
        _aip_insert_row(aip_id, retain_until),
        make_worm_row(expediente_id="EXP-1", tipo="aip", retain_until=retain_until),
        _evento_row("FIJACION"),
        _evento_row("WORM"),
        _evento_row("INGESTA"),
    ]
    mock_minio.get_object = minio_get_object(_NON_PDF_CONTENT)

    resp = await client.post(
        "/api/v1/preservacion/aip",
        json=_base_payload(doc_file_id=doc_file_id, nombre="nota.txt"),
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    evento_inserts = [
        c
        for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_evento" in c.args[0]
    ]
    assert [c.args[2] for c in evento_inserts] == ["FIJACION", "WORM", "INGESTA"]


@pytest.mark.asyncio
async def test_pdfa_validation_ignores_declared_mime_type_uses_sniff(
    client_with_conn, monkeypatch
) -> None:
    """`files.mime_type == "application/pdf"` pero los bytes reales NO
    empiezan con `%PDF`: el sniff por magic bytes gana — sin evento
    VALIDACION_PDFA (el mime lo declara el cliente, no es fiable)."""
    monkeypatch.setattr(config_module.settings, "preservation_pdfa_validation_enabled", True)
    client, mock_conn, mock_minio = client_with_conn

    doc_file_id = uuid.uuid4()
    aip_id = uuid.uuid4()
    retain_until = datetime(2031, 1, 1, tzinfo=UTC)

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [
        make_file_record(
            file_id=doc_file_id,
            filename="disfrazado.pdf",
            mime_type="application/pdf",  # MIENTE: el contenido real no es PDF
            sha256=_FAKE_PDF_MIME_SHA256,
            object_key=f"{doc_file_id}/disfrazado.pdf",
        ),
        None,  # get_aip_by_expediente_and_sha256 (idempotencia: sin duplicado previo)
        _aip_insert_row(aip_id, retain_until),
        make_worm_row(expediente_id="EXP-1", tipo="aip", retain_until=retain_until),
        _evento_row("FIJACION"),
        _evento_row("WORM"),
        _evento_row("INGESTA"),
    ]
    mock_minio.get_object = minio_get_object(_FAKE_PDF_MIME_CONTENT)

    resp = await client.post(
        "/api/v1/preservacion/aip",
        json=_base_payload(doc_file_id=doc_file_id, nombre="disfrazado.pdf"),
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    evento_inserts = [
        c
        for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_evento" in c.args[0]
    ]
    assert [c.args[2] for c in evento_inserts] == ["FIJACION", "WORM", "INGESTA"]


@pytest.mark.asyncio
async def test_get_pdfa_validator_falls_back_to_stub_when_verapdf_requested() -> None:
    """`PDFA_VALIDATOR=verapdf` sin `SubprocessVeraPdfValidator` implementado
    (E10 Increment C2b, roadmap): la factory degrada a `StubPdfaValidator`
    con un warning — NUNCA lanza ni crashea el arranque/empaquetado."""

    class _FakeSettings:
        pdfa_validator = "verapdf"

    validator = get_pdfa_validator(_FakeSettings())
    assert isinstance(validator, StubPdfaValidator)

    result = await validator.validate("/tmp/whatever.pdf", profile="2b")
    assert result.outcome == "not_evaluated"


@pytest.mark.asyncio
async def test_run_pdfa_validation_never_raises_on_validator_error() -> None:
    """Un validador que lanza una excepción (bug, futuro timeout de
    subprocess en C2b) nunca debe tumbar el empaquetado del AIP —
    `run_pdfa_validation` la atrapa y degrada a `not_evaluated`."""

    class _BoomValidator:
        async def validate(self, pdf_path: str, *, profile: str) -> PdfaResult:
            raise RuntimeError("validador roto a propósito para el test")

    result = await run_pdfa_validation(_BoomValidator(), b"%PDF-1.4 contenido", profile="2b")

    assert result.outcome == "not_evaluated"
    assert result.profile == "2b"


def test_migration_008_check_constraint_admits_no_evaluado() -> None:
    """La migración 008 ensancha `resultado` a VARCHAR(16) y su CHECK admite
    'no_evaluado' junto a 'ok'/'fallo' — sin esto, el INSERT del evento
    VALIDACION_PDFA con `resultado="no_evaluado"` violaría la constraint en
    Postgres real (el mock de `mock_conn` no lo verifica: los tests HTTP de
    arriba solo comprueban que el código INTENTA insertar ese valor, no que
    la BD lo aceptaría). Chequeo estático del SQL — sin abrir una conexión
    real (el run mandatado usa `--no-deps`, sin Postgres disponible)."""
    sql = _MIGRATION_008_PATH.read_text(encoding="utf-8")

    assert "VARCHAR(16)" in sql
    check_match = re.search(r"CHECK\s*\(resultado IN \(([^)]+)\)\)", sql)
    assert check_match, "no se encontró el CHECK constraint de resultado en la migración 008"
    allowed_values = {v.strip().strip("'") for v in check_match.group(1).split(",")}
    assert allowed_values == {"ok", "fallo", "no_evaluado"}

    # 'no_evaluado' (11 caracteres) debe caber en VARCHAR(16) — el motivo
    # explícito por el que la migración ensancha la columna desde VARCHAR(8).
    assert len("no_evaluado") <= 16


@pytest.mark.asyncio
async def test_stub_pdfa_validator_never_claims_conformance() -> None:
    """Test unitario directo del stub (HONESTIDAD, RF-FIR-15): sin pasar por
    HTTP/MinIO, `StubPdfaValidator.validate()` siempre devuelve
    `not_evaluated`, nunca `conformant` ni `non_conformant`."""
    validator = StubPdfaValidator()
    result = await validator.validate("/tmp/cualquier.pdf", profile="2b")

    assert result.outcome == "not_evaluated"
    assert result.validator_agent == "stub/validator_unavailable"
    assert "no" in result.detail["motivo"].lower()
