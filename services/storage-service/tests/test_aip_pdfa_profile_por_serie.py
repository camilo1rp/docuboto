# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Perfil PDF/A configurable por serie/TRD (E10 debt "perfil PDF/A por
serie", follow-up de Increment C2a/C2b, RF-FIR-15): `AipService.
_resolve_pdfa_profile` y `AipCreate.pdfa_profile` (frontera de confianza
archive-service -> storage-service, D-02).

INVARIANTE CENTRAL bajo prueba (fail-closed HONESTO, RF-FIR-15):
``invalid != default`` -- un perfil fuera de la whitelist NUNCA se sustituye
por el default global (`settings.pdfa_profile`, hoy "2b"); se conserva TAL
CUAL para que el validador lo degrade a `not_evaluated`. Sustituirlo
silenciosamente sería el bug real a evitar: haría que un documento se
validara contra un perfil DISTINTO al que el archivista configuró en la
serie sin que nadie se enterase, y en el peor caso (con un validador real
que no reimplementara esta misma guarda) podría reportar `conformant`
contra un perfil que jamás corrió.

Tres niveles:
  1. `_resolve_pdfa_profile` puro (sin BD/HTTP/MinIO).
  2. Composición con `SubprocessVeraPdfValidator` real (sin subprocess: la
     whitelist se comprueba ANTES de invocar veraPDF) -- cierra el circuito
     entre "se conserva tal cual" y "nunca se evalúa/reporta conformidad".
  3. End-to-end HTTP (`client_with_conn`, mismo patrón que
     `test_aip_pdfa.py`): el perfil que declara `AipCreate.pdfa_profile`
     llega intacto al evento `VALIDACION_PDFA` (`detalle.profile`).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

import pytest

import app.core.config as config_module
from app.schemas.preservacion import AipCreate
from app.services.aip_service import _resolve_pdfa_profile
from app.services.pdfa.subprocess_verapdf import SubprocessVeraPdfValidator
from tests.conftest import TENANT_HEADERS, TENANT_SLUG, make_file_record, make_worm_row, minio_get_object

_PDF_CONTENT = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\ncontenido pdf real de prueba (pdfa_profile)\n%%EOF"
_PDF_SHA256 = hashlib.sha256(_PDF_CONTENT).hexdigest()


@pytest.fixture(autouse=True)
def _preservation_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module.settings, "preservation_enabled", True)


# ---------------------------------------------------------------------------
# 1. `_resolve_pdfa_profile` puro -- el invariante clave.
# ---------------------------------------------------------------------------


def test_resolve_pdfa_profile_none_falls_back_to_configured_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retrocompat: `AipCreate.pdfa_profile is None` (serie sin política
    propia, o llamador legacy) -> el default GLOBAL configurado, no un
    literal "2b" hardcodeado -- se monkeypatchea a un valor distinto para
    demostrar que de verdad lee `settings`, no una constante."""
    monkeypatch.setattr(config_module.settings, "pdfa_profile", "1b")
    assert _resolve_pdfa_profile(None) == "1b"


def test_resolve_pdfa_profile_none_defaults_to_2b_out_of_the_box() -> None:
    assert config_module.settings.pdfa_profile == "2b"
    assert _resolve_pdfa_profile(None) == "2b"


@pytest.mark.parametrize("profile", ["1a", "1b", "2a", "2b", "2u", "3a", "3b", "3u"])
def test_resolve_pdfa_profile_valid_non_null_is_used_verbatim(profile: str) -> None:
    assert _resolve_pdfa_profile(profile) == profile


@pytest.mark.parametrize("bad_profile", ["9z", "2B", "", "no-es-un-flavour", "1a; rm -rf /"])
def test_resolve_pdfa_profile_invalid_is_never_substituted_by_default(
    bad_profile: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EL invariante: un perfil fuera de whitelist se PRESERVA tal cual --
    jamás cae al default (aunque el default esté configurado a un valor
    plausible que un bug de "normalización" pudiera devolver por error)."""
    monkeypatch.setattr(config_module.settings, "pdfa_profile", "2b")
    resultado = _resolve_pdfa_profile(bad_profile)
    assert resultado == bad_profile
    assert resultado != "2b"  # NUNCA sustituido por el default


def test_resolve_pdfa_profile_invalid_logs_warning_but_does_not_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING"):
        resultado = _resolve_pdfa_profile("9z")
    assert resultado == "9z"
    assert any("9z" in record.getMessage() for record in caplog.records)


# ---------------------------------------------------------------------------
# 2. Composición: perfil inválido -> `SubprocessVeraPdfValidator` -> NUNCA
# `conformant`, siempre `not_evaluated` (whitelist comprobada antes de
# invocar el subprocess -- no hace falta un binario real).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_profile_never_reaches_conformant_via_subprocess_validator() -> None:
    validator = SubprocessVeraPdfValidator(
        binary="/opt/verapdf/verapdf",
        timeout_seconds=5,
        max_concurrency=1,
        jvm_max_heap_mb=256,
    )
    resolved = _resolve_pdfa_profile("9z")  # invariante: pasa tal cual

    result = await validator.validate("/tmp/whatever.pdf", profile=resolved)

    assert result.outcome == "not_evaluated"
    assert result.outcome != "conformant"
    assert result.profile == "9z"


@pytest.mark.asyncio
async def test_missing_profile_resolves_to_default_and_would_be_evaluated_against_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Contraste positivo: `None` SÍ resuelve a un flavour reconocido por
    veraPDF (pasa la whitelist) -- solo el valor INVÁLIDO queda congelado en
    `not_evaluated` por construcción."""
    from app.services.pdfa.subprocess_verapdf import _VALID_FLAVOURS

    monkeypatch.setattr(config_module.settings, "pdfa_profile", "2b")
    resolved = _resolve_pdfa_profile(None)
    assert resolved in _VALID_FLAVOURS


# ---------------------------------------------------------------------------
# 3. `AipCreate.pdfa_profile` -- deliberadamente PERMISIVO (sin whitelist
# Pydantic): un valor fuera de whitelist NO aborta el AIP completo con 422.
# ---------------------------------------------------------------------------


def _aip_create_kwargs(**overrides) -> dict:
    """Insumos MÍNIMOS válidos de `AipCreate` (retención vía
    `retencion_anios`+`fecha_inicio`, `RetentionMixin`) para que un
    `ValidationError` en estos tests sea inequívocamente sobre
    `pdfa_profile`, no sobre otro campo requerido."""
    base = {
        "expediente_id": "EXP-1",
        "documentos": [{"document_id": "R-1", "nombre": "a.pdf", "file_id": str(uuid.uuid4())}],
        "retencion_anios": 5,
        "fecha_inicio": "2026-01-01",
    }
    base.update(overrides)
    return base


def test_aip_create_accepts_any_short_string_no_whitelist() -> None:
    payload = AipCreate(**_aip_create_kwargs(pdfa_profile="9z"))  # fuera de whitelist -- NO debe lanzar
    assert payload.pdfa_profile == "9z"


def test_aip_create_accepts_null_pdfa_profile() -> None:
    payload = AipCreate(**_aip_create_kwargs())
    assert payload.pdfa_profile is None


def test_aip_create_rejects_pdfa_profile_over_max_length() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as exc_info:
        AipCreate(**_aip_create_kwargs(pdfa_profile="x" * 33))
    assert "pdfa_profile" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 4. End-to-end HTTP: el perfil declarado llega intacto al evento
# VALIDACION_PDFA (`detalle.profile`) -- mismo montaje que `test_aip_pdfa.py`.
# ---------------------------------------------------------------------------


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


def _base_payload(*, doc_file_id: uuid.UUID, pdfa_profile: str | None) -> dict:
    body = {
        "expediente_id": "EXP-1",
        "documentos": [{"document_id": "R-1", "nombre": "oficio.pdf", "file_id": str(doc_file_id)}],
        "retencion_anios": 5,
        "fecha_inicio": "2026-01-01",
        "legal_hold": False,
    }
    if pdfa_profile is not None:
        body["pdfa_profile"] = pdfa_profile
    return body


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


async def _run_pdfa_e2e(
    client_with_conn, monkeypatch: pytest.MonkeyPatch, *, pdfa_profile: str | None
):
    """Ejercita `POST /api/v1/preservacion/aip` con el gate PDF/A activo y
    devuelve el `detalle` (dict) del evento VALIDACION_PDFA insertado."""
    import json

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
        json=_base_payload(doc_file_id=doc_file_id, pdfa_profile=pdfa_profile),
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201, resp.text

    evento_inserts = [
        c
        for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_evento" in c.args[0]
    ]
    pdfa_call = next(c for c in evento_inserts if c.args[2] == "VALIDACION_PDFA")
    # insert_evento() params: (sql, object_ref, tipo, resultado, hash_before, hash_after, detalle, agente)
    return pdfa_call.args[3], json.loads(pdfa_call.args[6])  # (resultado, detalle)


@pytest.mark.asyncio
async def test_e2e_serie_profile_reaches_validacion_pdfa_event(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El perfil de la serie ('2u') -- resuelto por archive-service y
    reenviado en `AipCreate.pdfa_profile` -- es el que aparece en el evento
    VALIDACION_PDFA, no el default global."""
    resultado, detalle = await _run_pdfa_e2e(client_with_conn, monkeypatch, pdfa_profile="2u")
    assert detalle["profile"] == "2u"
    assert resultado == "no_evaluado"  # stub honesto -- jamás "ok" sin validador real


@pytest.mark.asyncio
async def test_e2e_missing_profile_falls_back_to_global_default(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retrocompat: sin `pdfa_profile` en el payload (serie sin política
    propia), el evento queda validado contra el default global ("2b")."""
    monkeypatch.setattr(config_module.settings, "pdfa_profile", "2b")
    resultado, detalle = await _run_pdfa_e2e(client_with_conn, monkeypatch, pdfa_profile=None)
    assert detalle["profile"] == "2b"
    assert resultado == "no_evaluado"


@pytest.mark.asyncio
async def test_e2e_invalid_profile_never_reported_conformant_and_is_preserved_verbatim(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RF-FIR-15, el caso adversarial central: un `pdfa_profile` fuera de
    whitelist ('9z') que llegara hasta aquí (p. ej. archive-service con un
    bug, o un llamador interno malicioso/mal configurado) NUNCA se sustituye
    por el default ni se reporta `ok`/conformidad -- queda registrado TAL
    CUAL en el evento, con `resultado='no_evaluado'`."""
    resultado, detalle = await _run_pdfa_e2e(client_with_conn, monkeypatch, pdfa_profile="9z")
    assert detalle["profile"] == "9z"
    assert detalle["profile"] != "2b"
    assert resultado == "no_evaluado"
    assert resultado != "ok"
