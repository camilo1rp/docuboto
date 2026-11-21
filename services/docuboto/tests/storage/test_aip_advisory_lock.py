# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Advisory lock de sesión de `AipService.empaquetar_aip` (remediación de
auditoría Media, cierre de la carrera de doble-empaquetado WORM
irreversible — E10-B, ver migración
`012_preservacion_worm_aip_idempotencia.sql`).

Carril UNIT (mock_conn/mock_minio, ver conftest.py): verifica que el lock se
adquiere/libera con la MISMA clave que `PreservationWormService.
proteger_indice` usa para `tipo="indice"` (namespace distinto para
`tipo="aip"`), que `storage.api.package_aip` (acto de sistema) lo ejerce, y que el corte-circuito idempotente por contenido (`aip_sha256`
exacto ya registrado) no sube nada a MinIO ni inserta una fila nueva.

El carril de INTEGRACIÓN (dos conexiones Postgres reales, empaquetados
concurrentes genuinos) vive en
`tests/integration/test_aip_concurrent_real_sql.py`.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, date, datetime

import pytest

import storage.services.aip_service as aip_service_module
from docuboto import config as config_module
from storage import api as storage_api
from storage.schemas.preservacion import AipCreate, AipResponse
from storage.services.aip_service import AipService
from storage.services.preservation_worm_service import FixityMismatchError, _worm_lock_key
from tests.storage.conftest import (
    TENANT_SLUG,
    make_file_record,
    make_worm_row,
    minio_get_object,
)

_DOC_CONTENT = b"contenido real del oficio radicado"
_DOC_SHA256 = hashlib.sha256(_DOC_CONTENT).hexdigest()


@pytest.fixture(autouse=True)
def _preservation_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module.storage, "preservation_enabled", True)


async def _empaquetar(mock_conn, payload: dict) -> AipResponse:
    return await AipService(mock_conn, TENANT_SLUG).empaquetar_aip(
        AipCreate(**payload), actor="user-1"
    )


def _doc_record(file_id: uuid.UUID) -> dict:
    return make_file_record(
        file_id=file_id,
        filename="oficio.pdf",
        mime_type="application/pdf",
        sha256=_DOC_SHA256,
        object_key=f"{file_id}/oficio.pdf",
    )


def _base_payload(doc_file_id: uuid.UUID, **overrides) -> dict:
    payload = {
        "expediente_id": "EXP-1",
        "documentos": [
            {"document_id": "R-1", "nombre": "oficio.pdf", "file_id": str(doc_file_id)}
        ],
        "retencion_anios": 5,
        "fecha_inicio": "2026-01-01",
    }
    payload.update(overrides)
    return payload


def _aip_insert_row(aip_id: uuid.UUID, retain_until: datetime, *, aip_sha256: str = "c" * 64) -> dict:
    return {
        "id": aip_id,
        "expediente_id": "EXP-1",
        "premis_xml": "<premis/>",
        "aip_sha256": aip_sha256,
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


def _lock_calls(mock_conn):
    return [
        c for c in mock_conn.execute.call_args_list if c.args and "pg_advisory_lock" in c.args[0]
    ]


def _unlock_calls(mock_conn):
    return [
        c for c in mock_conn.execute.call_args_list if c.args and "pg_advisory_unlock" in c.args[0]
    ]


# ---------------------------------------------------------------------------
# Clave del lock: namespace `tipo="aip"` distinto de `tipo="indice"`
# ---------------------------------------------------------------------------


def test_aip_lock_key_uses_tipo_aip_and_differs_from_indice() -> None:
    """`AipService.empaquetar_aip` reusa `_worm_lock_key` de
    `preservation_worm_service.py` con `tipo="aip"` -- distinto namespace del
    que usa `proteger_indice` (`tipo="indice"`), para que proteger el índice
    y empaquetar el AIP del MISMO expediente no se serialicen innecesariamente
    entre sí (artefactos independientes)."""
    k_aip = _worm_lock_key("icetex", "EXP-1", "aip")
    k_indice = _worm_lock_key("icetex", "EXP-1", "indice")
    assert k_aip != k_indice
    assert k_aip == _worm_lock_key("icetex", "EXP-1", "aip")  # estable
    assert -(2**63) <= k_aip < 2**63  # rango de pg_advisory_lock(bigint)


# ---------------------------------------------------------------------------
# Adquisición/liberación del lock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empaquetar_aip_acquires_and_releases_advisory_lock(client_with_conn) -> None:
    """El happy path toma `pg_advisory_lock` y lo libera (`pg_advisory_unlock`)
    con la MISMA clave -- serialización por-(expediente, 'aip') del
    check->put_object->insert."""
    _client, mock_conn, mock_minio = client_with_conn
    doc_file_id = uuid.uuid4()
    aip_id = uuid.uuid4()
    retain_until = datetime(2031, 1, 1, tzinfo=UTC)

    mock_conn.fetchrow.side_effect = [
        _doc_record(doc_file_id),  # get_by_id_any_state
        None,  # get_aip_by_expediente_and_sha256: sin duplicado previo
        _aip_insert_row(aip_id, retain_until),  # insert_aip RETURNING
        make_worm_row(expediente_id="EXP-1", tipo="aip", retain_until=retain_until),
        _evento_row("FIJACION"),
        _evento_row("WORM"),
        _evento_row("INGESTA"),
    ]
    mock_minio.get_object = minio_get_object(_DOC_CONTENT)

    await _empaquetar(mock_conn, _base_payload(doc_file_id))

    lock_calls = _lock_calls(mock_conn)
    unlock_calls = _unlock_calls(mock_conn)
    assert len(lock_calls) == 1
    assert len(unlock_calls) == 1
    assert lock_calls[0].args[1] == unlock_calls[0].args[1]
    assert lock_calls[0].args[1] == _worm_lock_key(TENANT_SLUG, "EXP-1", "aip")


@pytest.mark.asyncio
async def test_empaquetar_aip_releases_lock_on_fixity_mismatch(client_with_conn) -> None:
    """El lock se libera aun cuando `_resolve_and_verify` aborta con
    `FixityMismatchError` -- el `finally` de
    `empaquetar_aip` no depende de que el camino feliz haya terminado."""
    _client, mock_conn, mock_minio = client_with_conn
    doc_file_id = uuid.uuid4()

    mock_conn.fetchrow.side_effect = [
        _doc_record(doc_file_id),  # get_by_id_any_state
        {"id": uuid.uuid4()},  # insert_evento FIJACION fallo
    ]
    mock_minio.get_object = minio_get_object(b"bytes alterados, no coinciden")

    with pytest.raises(FixityMismatchError):
        await _empaquetar(mock_conn, _base_payload(doc_file_id))

    assert len(_lock_calls(mock_conn)) == 1
    assert len(_unlock_calls(mock_conn)) == 1


# ---------------------------------------------------------------------------
# El acto de sistema (`storage.api.package_aip`) comparte el MISMO lock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_package_aip_also_acquires_lock(client_with_conn) -> None:
    """`storage.api.package_aip` (reconciliación, SYSTEM_RECONCILER_ID) llama al
    MISMO `AipService.empaquetar_aip` -- el advisory lock lo cubre también."""
    _client, mock_conn, mock_minio = client_with_conn
    doc_file_id = uuid.uuid4()
    aip_id = uuid.uuid4()
    retain_until = datetime(2031, 1, 1, tzinfo=UTC)

    mock_conn.fetchrow.side_effect = [
        _doc_record(doc_file_id),
        None,  # get_aip_by_expediente_and_sha256: sin duplicado previo
        _aip_insert_row(aip_id, retain_until),
        make_worm_row(expediente_id="EXP-1", tipo="aip", retain_until=retain_until),
        _evento_row("FIJACION"),
        _evento_row("WORM"),
        _evento_row("INGESTA"),
    ]
    mock_minio.get_object = minio_get_object(_DOC_CONTENT)

    outcome = await storage_api.package_aip(
        tenant_slug=TENANT_SLUG,
        expediente_id="EXP-1",
        documentos=_base_payload(doc_file_id)["documentos"],
        indice_file_id=None,
        retencion_anios=5,
        fecha_inicio=date(2026, 1, 1),
    )
    assert outcome.ok, outcome
    assert outcome.aip_id == aip_id

    lock_calls = _lock_calls(mock_conn)
    assert len(lock_calls) == 1
    assert lock_calls[0].args[1] == _worm_lock_key(TENANT_SLUG, "EXP-1", "aip")


# ---------------------------------------------------------------------------
# Corte-circuito idempotente por contenido (mismo `aip_sha256`)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empaquetar_aip_idempotent_shortcircuit_on_identical_content(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bajo el lock, si el `aip_sha256` recién construido YA está registrado
    para el mismo expediente (dos empaquetados serializados por el lock que
    -- por lo que sea -- produjeron bytes idénticos), `empaquetar_aip`
    corta-circuita: NO sube nada a MinIO, NO inserta una fila nueva, devuelve
    el AIP EXISTENTE -- mismo criterio de idempotencia por-contenido que la
    migración 012 documenta.

    `aip_sha256` (el hash del ZIP completo) se fija con un monkeypatch para
    que el test sea determinista sin depender de que el Bag real produzca por
    casualidad el mismo hash en dos builds (normalmente NO lo hace, ver el
    comentario corregido de la migración 012 -- el hash embebe un timestamp).
    """
    _client, mock_conn, mock_minio = client_with_conn
    doc_file_id = uuid.uuid4()
    existing_aip_id = uuid.uuid4()
    retain_until = datetime(2031, 1, 1, tzinfo=UTC)
    fixed_hash = "d" * 64

    monkeypatch.setattr(aip_service_module, "aip_sha256", lambda _zip_bytes: fixed_hash)

    existing_row = _aip_insert_row(existing_aip_id, retain_until, aip_sha256=fixed_hash)

    mock_conn.fetchrow.side_effect = [
        _doc_record(doc_file_id),  # get_by_id_any_state
        existing_row,  # get_aip_by_expediente_and_sha256: YA existe
    ]
    mock_minio.get_object = minio_get_object(_DOC_CONTENT)

    result = await _empaquetar(mock_conn, _base_payload(doc_file_id))

    # El corte-circuito devuelve el AIP YA existente, sin distinguirlo de uno nuevo.
    assert result.id == existing_aip_id
    assert result.aip_sha256 == fixed_hash

    mock_minio.put_object.assert_not_awaited()
    mock_minio.make_bucket.assert_not_awaited()

    insert_calls = [
        c
        for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO aip" in c.args[0]
    ]
    assert insert_calls == []

    assert len(_unlock_calls(mock_conn)) == 1  # liberado pese al corte-circuito


@pytest.mark.asyncio
async def test_empaquetar_aip_different_content_does_not_shortcircuit(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un `aip_sha256` que NO coincide con ningún registro previo procede a
    empaquetar normalmente (re-empaquetado legítimo, migración 009) -- el
    corte-circuito es estrictamente por contenido EXACTO, no por
    `expediente_id` a secas."""
    _client, mock_conn, mock_minio = client_with_conn
    doc_file_id = uuid.uuid4()
    aip_id = uuid.uuid4()
    retain_until = datetime(2031, 1, 1, tzinfo=UTC)

    monkeypatch.setattr(aip_service_module, "aip_sha256", lambda _zip_bytes: "e" * 64)

    mock_conn.fetchrow.side_effect = [
        _doc_record(doc_file_id),
        None,  # get_aip_by_expediente_and_sha256: ningún row con ESTE sha256
        _aip_insert_row(aip_id, retain_until, aip_sha256="e" * 64),
        make_worm_row(expediente_id="EXP-1", tipo="aip", retain_until=retain_until),
        _evento_row("FIJACION"),
        _evento_row("WORM"),
        _evento_row("INGESTA"),
    ]
    mock_minio.get_object = minio_get_object(_DOC_CONTENT)

    await _empaquetar(mock_conn, _base_payload(doc_file_id))

    mock_minio.put_object.assert_awaited_once()
