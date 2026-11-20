# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests de la protección WORM del índice firmado (E10 Increment A, RF-PRE-03).

Desde ADR-047 no hay ruta HTTP: se llama a `PreservationWormService.proteger_indice`
(lo que hacía la ruta, con `tipo='indice'` forzado) o a `storage.api.protect_index*`.

Ningún test escribe objetos COMPLIANCE reales — MinIO está completamente mockeado
(`tests/conftest.py::_make_mock_minio`); solo se asertan las LLAMADAS (make_bucket,
put_object, get_object_lock_config, etc.) y sus argumentos.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pydantic
import pytest
from miniopy_async import S3Error  # type: ignore[attr-defined]

import storage.core.config as config_module
from storage import api as storage_api
from storage.core.constants import SYSTEM_RECONCILER_ID
from storage.core.minio_client import ObjectLockNotEnabledError
from storage.schemas.preservacion import ProtegerIndiceRequest
from storage.services.preservation_worm_service import (
    FileNotFoundForPreservationError,
    FixityMismatchError,
    PreservationDisabledError,
    PreservationWormService,
)
from storage.services.retention_policy import InvalidRetentionError
from tests.storage.conftest import (
    TENANT_SLUG,
    make_file_record,
    make_worm_row,
)

_INDICE_XML = b"<indice>firmado</indice>"
_INDICE_SHA256 = hashlib.sha256(_INDICE_XML).hexdigest()


@pytest.fixture(autouse=True)
def _preservation_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """PRESERVATION_ENABLED=false es el default seguro (kill-switch) — se activa
    aquí para poder ejercer la protección; `test_proteger_indice_disabled_by_feature_flag`
    lo vuelve a apagar explícitamente."""
    monkeypatch.setattr(config_module.settings, "preservation_enabled", True)


def _grant_permission(mock_conn) -> None:
    mock_conn.fetchval.return_value = True


def _file_record(file_id: uuid.UUID) -> dict:
    return make_file_record(
        file_id=file_id,
        filename="indice.xml",
        mime_type="application/xml",
        sha256=_INDICE_SHA256,
        object_key=f"{file_id}/indice.xml",
    )


def _minio_get_object(content: bytes) -> AsyncMock:
    obj = MagicMock()
    obj.read = AsyncMock(return_value=content)
    obj.close = MagicMock()
    return AsyncMock(return_value=obj)


def _base_payload(file_id: uuid.UUID, **overrides) -> dict:
    payload = {
        "file_id": str(file_id),
        "expediente_id": "EXP-1",
        "retencion_anios": 5,
        "fecha_inicio": "2026-01-01",
    }
    payload.update(overrides)
    return payload


async def _proteger(mock_conn, payload: dict, actor: str | None = "actor"):
    """Lo que hacía la ruta: valida el body, fuerza `tipo='indice'` y llama al
    servicio. Devuelve `(respuesta, created)`."""
    request = ProtegerIndiceRequest(**payload).model_copy(update={"tipo": "indice"})
    return await PreservationWormService(mock_conn, TENANT_SLUG).proteger_indice(
        request, actor=actor
    )


@pytest.mark.asyncio
async def test_proteger_indice_happy_path(client_with_conn) -> None:
    _client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    record = _file_record(file_id)
    worm_row = make_worm_row(expediente_id="EXP-1", object_key=record["object_key"])

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [
        None,  # PreservacionWormRepository.get_by_expediente_and_tipo (idempotencia: nada previo)
        record,  # FileRepository.get_by_id_any_state
        worm_row,  # PreservacionWormRepository.insert (RETURNING)
        {"id": uuid.uuid4()},  # insert_evento FIJACION ok
        {"id": uuid.uuid4()},  # insert_evento WORM ok
    ]
    mock_minio.get_object = _minio_get_object(_INDICE_XML)

    response, created = await _proteger(mock_conn, _base_payload(file_id))

    assert created is True
    body = response.model_dump(mode="json")
    assert body["expediente_id"] == "EXP-1"
    assert body["bucket"] == f"orpycamcp-{TENANT_SLUG}-preservation"
    assert body["sha256"] == _INDICE_SHA256
    assert body["retention_mode"] == "COMPLIANCE"

    # Bucket ya existía CON lock (default del mock) → no vuelve a crearlo.
    mock_minio.make_bucket.assert_not_awaited()
    mock_minio.get_object_lock_config.assert_awaited_once_with(
        f"orpycamcp-{TENANT_SLUG}-preservation"
    )

    put_call = mock_minio.put_object.await_args
    assert put_call.args[0] == f"orpycamcp-{TENANT_SLUG}-preservation"
    assert put_call.args[1] == record["object_key"]
    retention = put_call.kwargs["retention"]
    assert retention.mode == "COMPLIANCE"
    assert retention.retain_until_date == datetime(2031, 1, 1, tzinfo=UTC)
    assert put_call.kwargs["legal_hold"] is False

    # Eventos PREMIS: FIJACION ok (fixity confirmada) + WORM ok (subida inmutable),
    # en ese orden — ambos insertados ANTES del audit_log final.
    evento_inserts = [
        c
        for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_evento" in c.args[0]
    ]
    assert len(evento_inserts) == 2
    fijacion_call, worm_call = evento_inserts
    # insert_evento params: (sql, object_ref, tipo, resultado, hash_before, hash_after, detalle, agente)
    assert fijacion_call.args[2] == "FIJACION"
    assert fijacion_call.args[3] == "ok"
    assert worm_call.args[2] == "WORM"
    assert worm_call.args[3] == "ok"

    # audit_log inmutable: la protección WORM deja traza propia (`file.worm_protegido`).
    audit_inserts = [
        c
        for c in mock_conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_inserts) == 1
    # _INSERT_SQL params: (sql, tenant, service, actor, canal, action, object_type, object_ref, payload, prev_hash, hash)
    audit_args = audit_inserts[0].args
    assert audit_args[1] == TENANT_SLUG
    assert audit_args[2] == "storage-service"
    assert audit_args[5] == "file.worm_protegido"
    assert audit_args[6] == "file"
    assert audit_args[7] == str(file_id)


@pytest.mark.asyncio
async def test_proteger_indice_happy_path_direct_retain_until(client_with_conn) -> None:
    """Rama `retain_until` directo (el llamador ya calculó la fecha absoluta),
    en vez de `retencion_anios` + `fecha_inicio`."""
    _client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    record = _file_record(file_id)
    worm_row = make_worm_row(expediente_id="EXP-1", object_key=record["object_key"])

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [
        None,
        record,
        worm_row,
        {"id": uuid.uuid4()},
        {"id": uuid.uuid4()},
    ]
    mock_minio.get_object = _minio_get_object(_INDICE_XML)

    direct_until = "2032-06-15T00:00:00Z"
    _response, created = await _proteger(
        mock_conn,
        _base_payload(file_id, retencion_anios=None, fecha_inicio=None, retain_until=direct_until),
    )

    assert created is True
    put_call = mock_minio.put_object.await_args
    retention = put_call.kwargs["retention"]
    assert retention.retain_until_date == datetime(2032, 6, 15, tzinfo=UTC)


@pytest.mark.asyncio
async def test_proteger_indice_legal_hold_en_el_body_es_invalido(client_with_conn) -> None:
    """AREA-08-07: `legal_hold` ya no se declara al proteger (no tenía acto
    inverso y bloqueaba la disposición para siempre). Ni siquiera `false`."""
    _client, mock_conn, mock_minio = client_with_conn
    for valor in (True, False):
        with pytest.raises(pydantic.ValidationError):
            await _proteger(mock_conn, _base_payload(uuid.uuid4(), legal_hold=valor))
    mock_minio.put_object.assert_not_awaited()
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_proteger_indice_rejects_retain_until_beyond_max_years(client_with_conn) -> None:
    """`retain_until` futuro pero por encima del techo PRESERVATION_MAX_RETENTION_YEARS
    (100 años) — probable bug de unidades del llamador, no una TRD real."""
    _client, mock_conn, mock_minio = client_with_conn
    _grant_permission(mock_conn)
    too_far = (datetime.now(tz=UTC) + timedelta(days=365 * 200)).isoformat()

    with pytest.raises(InvalidRetentionError):
        await _proteger(
            mock_conn,
            _base_payload(
                uuid.uuid4(), retencion_anios=None, fecha_inicio=None, retain_until=too_far
            ),
        )

    mock_conn.fetchrow.assert_not_called()  # falla antes de tocar BD/MinIO
    mock_minio.put_object.assert_not_awaited()


@pytest.mark.asyncio
async def test_proteger_indice_provisions_bucket_when_absent(client_with_conn) -> None:
    """Bucket ausente → se crea con object_lock=True (PIEZA 2 caso a)."""
    _client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    record = _file_record(file_id)
    worm_row = make_worm_row(object_key=record["object_key"])

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [
        None, record, worm_row, {"id": uuid.uuid4()}, {"id": uuid.uuid4()}
    ]
    mock_minio.get_object = _minio_get_object(_INDICE_XML)
    mock_minio.bucket_exists = AsyncMock(return_value=False)

    response, created = await _proteger(mock_conn, _base_payload(file_id))

    assert created is True
    mock_minio.make_bucket.assert_awaited_once_with(
        f"orpycamcp-{TENANT_SLUG}-preservation", object_lock=True
    )
    mock_minio.get_object_lock_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_proteger_indice_bucket_exists_without_lock_is_rejected(client_with_conn) -> None:
    """Bucket existente SIN Object Lock → error ruidoso, se aborta (PIEZA 2 caso c)."""
    _client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    record = _file_record(file_id)

    _grant_permission(mock_conn)
    # side_effect (no return_value constante): la 1ra llamada es la
    # idempotencia (get_by_expediente_and_tipo, debe dar `None` — sin fila
    # previa), la 2da es FileRepository.get_by_id_any_state.
    mock_conn.fetchrow.side_effect = [None, record]
    mock_minio.get_object = _minio_get_object(_INDICE_XML)
    mock_minio.get_object_lock_config = AsyncMock(
        side_effect=S3Error(
            "ObjectLockConfigurationNotFoundError",
            "Object Lock configuration does not exist",
            "resource",
            "req-id",
            "host-id",
            MagicMock(),
        )
    )

    with pytest.raises(ObjectLockNotEnabledError):
        await _proteger(mock_conn, _base_payload(file_id))

    mock_minio.put_object.assert_not_awaited()


@pytest.mark.asyncio
async def test_proteger_indice_fixity_mismatch_blocks_upload(client_with_conn) -> None:
    _client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    record = _file_record(file_id)

    _grant_permission(mock_conn)
    # idempotencia (None) + file + evento FIJACION fallo
    mock_conn.fetchrow.side_effect = [None, record, {"id": uuid.uuid4()}]
    mock_minio.get_object = _minio_get_object(b"contenido alterado, no coincide")

    with pytest.raises(FixityMismatchError):
        await _proteger(mock_conn, _base_payload(file_id))

    mock_minio.put_object.assert_not_awaited()
    mock_minio.make_bucket.assert_not_awaited()

    # El evento FIJACION queda registrado como fallo.
    insert_evt_call = mock_conn.fetchrow.call_args_list[-1]
    assert "INSERT INTO preservacion_evento" in insert_evt_call.args[0]
    assert insert_evt_call.args[3] == "fallo"  # (sql, object_ref, tipo, resultado, ...)

    # Un mismatch de fixity también deja traza en el audit_log inmutable
    # hash-encadenado — no solo en preservacion_evento (hallazgo de auditoría
    # Increment A: la traza más sensible del flujo no puede quedar solo en la
    # tabla mutable de eventos PREMIS).
    audit_inserts = [
        c
        for c in mock_conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_inserts) == 1
    # (sql, tenant_slug, service, actor, canal, action, object_type, object_ref, payload, ...)
    assert audit_inserts[0].args[5] == "file.fixity_fallo"
    assert audit_inserts[0].args[6] == "file"
    assert audit_inserts[0].args[7] == str(file_id)


@pytest.mark.asyncio
async def test_proteger_indice_canonicalizes_expediente_id_whitespace(client_with_conn) -> None:
    """El `expediente_id` se normaliza (trim + colapso de espacios) ANTES de
    persistirse — es la misma clave que usará el gate de purga en disposición
    final; una diferencia incidental de espacios entre ambas llamadas
    rompería el gate (hallazgo de auditoría Increment A)."""
    _client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    record = _file_record(file_id)
    worm_row = make_worm_row(expediente_id="EXP  RARO", object_key=record["object_key"])

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [
        None,
        record,
        worm_row,
        {"id": uuid.uuid4()},
        {"id": uuid.uuid4()},
    ]
    mock_minio.get_object = _minio_get_object(_INDICE_XML)

    response, created = await _proteger(
        mock_conn, _base_payload(file_id, expediente_id="  EXP   RARO  ")
    )

    assert created is True
    assert response.expediente_id == "EXP RARO"

    insert_call = [
        c
        for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_worm_objeto" in c.args[0]
    ][0]
    # insert() params: (sql, expediente_id, tipo, bucket, ...)
    assert insert_call.args[1] == "EXP RARO"


def test_proteger_indice_rejects_blank_expediente_id() -> None:
    with pytest.raises(pydantic.ValidationError):
        ProtegerIndiceRequest(**_base_payload(uuid.uuid4(), expediente_id="   "))


@pytest.mark.asyncio
async def test_proteger_indice_file_not_found(client_with_conn) -> None:
    _client, mock_conn, mock_minio = client_with_conn
    _grant_permission(mock_conn)
    mock_conn.fetchrow.return_value = None

    with pytest.raises(FileNotFoundForPreservationError):
        await _proteger(mock_conn, _base_payload(uuid.uuid4()))


@pytest.mark.asyncio
async def test_proteger_indice_rejects_past_retain_until(client_with_conn) -> None:
    _client, mock_conn, mock_minio = client_with_conn
    _grant_permission(mock_conn)

    with pytest.raises(InvalidRetentionError):
        await _proteger(
            mock_conn,
            _base_payload(
                uuid.uuid4(),
                retencion_anios=None,
                fecha_inicio=None,
                retain_until="2020-01-01T00:00:00Z",
            ),
        )

    mock_conn.fetchrow.assert_not_called()  # falla antes de tocar BD/MinIO


@pytest.mark.asyncio
async def test_proteger_indice_rejects_retention_below_floor(client_with_conn) -> None:
    """retain_until futuro pero por debajo del piso PRESERVATION_MIN_RETENTION_DAYS (365)."""
    _client, mock_conn, mock_minio = client_with_conn
    _grant_permission(mock_conn)
    soon = (datetime.now(tz=UTC) + timedelta(days=5)).isoformat()

    with pytest.raises(InvalidRetentionError):
        await _proteger(
            mock_conn,
            _base_payload(uuid.uuid4(), retencion_anios=None, fecha_inicio=None, retain_until=soon),
        )


@pytest.mark.asyncio
async def test_proteger_indice_bucket_name_derived_from_tenant_arg_not_body(client_with_conn) -> None:
    """El nombre del bucket WORM sale del tenant que recibe el servicio, nunca del body —
    un `tenant_slug` colado en el body es un campo desconocido para el schema y
    se ignora (Pydantic v2 `extra="ignore"` por defecto)."""
    _client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    record = _file_record(file_id)
    worm_row = make_worm_row(object_key=record["object_key"])

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [
        None, record, worm_row, {"id": uuid.uuid4()}, {"id": uuid.uuid4()}
    ]
    mock_minio.get_object = _minio_get_object(_INDICE_XML)

    payload = _base_payload(file_id)
    payload["tenant_slug"] = "attacker-tenant"  # ignorado: no es un campo del schema

    response, created = await _proteger(mock_conn, payload)

    assert created is True
    put_call = mock_minio.put_object.await_args
    assert put_call.args[0] == f"orpycamcp-{TENANT_SLUG}-preservation"
    assert "attacker" not in put_call.args[0]


@pytest.mark.asyncio
async def test_proteger_indice_disabled_by_feature_flag(client_with_conn, monkeypatch) -> None:
    _client, mock_conn, mock_minio = client_with_conn
    _grant_permission(mock_conn)
    monkeypatch.setattr(config_module.settings, "preservation_enabled", False)

    with pytest.raises(PreservationDisabledError):
        await _proteger(mock_conn, _base_payload(uuid.uuid4()))

    mock_conn.fetchrow.assert_not_called()


# ── Idempotencia (E10 Inc.1 — cierre del cableado WORM) ──────────────────


@pytest.mark.asyncio
async def test_proteger_indice_second_call_is_idempotent_no_reupload(client_with_conn) -> None:
    """Segunda llamada al MISMO `expediente_id` (reintento tras timeout, o
    carrera intento-síncrono-vs-barrida): NO vuelve a tocar fixity/MinIO, NO
    inserta una segunda fila WORM — devuelve la fila YA protegida, `created=False`.
    """
    _client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    existing_worm_id = uuid.uuid4()
    existing_file_id = uuid.uuid4()
    existing = make_worm_row(
        worm_id=existing_worm_id, expediente_id="EXP-1", object_key=f"{existing_file_id}/indice.xml"
    )
    existing["file_id"] = file_id  # MISMO file_id: reintento legítimo

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [existing]  # get_by_expediente_and_tipo: YA existe

    response, created = await _proteger(mock_conn, _base_payload(file_id))

    assert created is False
    body = response.model_dump(mode="json")
    assert body["id"] == str(existing_worm_id)
    assert body["expediente_id"] == "EXP-1"

    # Ni fixity (get_object) ni la subida (put_object) se tocan.
    mock_minio.get_object.assert_not_called()
    mock_minio.put_object.assert_not_awaited()
    mock_minio.make_bucket.assert_not_awaited()

    # Solo UNA consulta de BD (el chequeo de idempotencia) — nada de INSERT.
    insert_calls = [
        c
        for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_worm_objeto" in c.args[0]
    ]
    assert insert_calls == []


@pytest.mark.asyncio
async def test_proteger_indice_second_call_different_expediente_is_not_idempotent(
    client_with_conn,
) -> None:
    """La idempotencia es POR `expediente_id` — un segundo `proteger-indice`
    para un expediente DISTINTO (aunque el mismo file_id/tenant) sigue
    subiendo normalmente (`created=True`), no se confunde con el candado del primero."""
    _client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    record = _file_record(file_id)
    worm_row = make_worm_row(expediente_id="EXP-2", object_key=record["object_key"])

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [
        None,  # get_by_expediente_and_tipo(EXP-2, indice) -- nada previo
        record,
        worm_row,
        {"id": uuid.uuid4()},
        {"id": uuid.uuid4()},
    ]
    mock_minio.get_object = _minio_get_object(_INDICE_XML)

    response, created = await _proteger(mock_conn, _base_payload(file_id, expediente_id="EXP-2"))

    assert created is True
    mock_minio.put_object.assert_awaited_once()


# ── Acto de sistema (storage.api, SYSTEM_RECONCILER_ID) ───────────────────


@pytest.mark.asyncio
async def test_protect_index_as_system_uses_system_actor(client_with_conn) -> None:
    """`storage.api.protect_index_as_system` usa el mismo servicio/idempotencia;
    el actor de auditoría es SIEMPRE SYSTEM_RECONCILER_ID."""
    _client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    record = _file_record(file_id)
    worm_row = make_worm_row(expediente_id="EXP-3", object_key=record["object_key"])

    mock_conn.fetchrow.side_effect = [
        None,
        record,
        worm_row,
        {"id": uuid.uuid4()},
        {"id": uuid.uuid4()},
    ]
    mock_minio.get_object = _minio_get_object(_INDICE_XML)

    outcome = await storage_api.protect_index_as_system(
        tenant_slug=TENANT_SLUG,
        file_id=str(file_id),
        expediente_id="EXP-3",
        retencion_anios=5,
        fecha_inicio=date(2026, 1, 1),
    )

    assert outcome.ok
    assert outcome.worm_object_id == worm_row["id"]
    mock_minio.put_object.assert_awaited_once()

    audit_inserts = [
        c
        for c in mock_conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_inserts) == 1
    assert audit_inserts[0].args[3] == str(SYSTEM_RECONCILER_ID)


@pytest.mark.asyncio
async def test_protect_index_as_system_second_call_is_idempotent(client_with_conn) -> None:
    """Mismo contrato de idempotencia: la 3ra fase de reconciliación
    (`run_once_worm`) puede reintentar sin re-subir."""
    _client, mock_conn, mock_minio = client_with_conn
    existing = make_worm_row(expediente_id="EXP-4")

    mock_conn.fetchrow.side_effect = [existing]

    outcome = await storage_api.protect_index_as_system(
        tenant_slug=TENANT_SLUG,
        file_id=str(uuid.uuid4()),
        expediente_id="EXP-4",
        retencion_anios=5,
        fecha_inicio=date(2026, 1, 1),
    )

    assert outcome.ok
    assert outcome.worm_object_id == existing["id"]
    mock_minio.put_object.assert_not_awaited()


# ---------------------------------------------------------------------------
# Advisory lock de sesión por-expediente (hallazgo Media de auditoría): serializa
# check->put_object->insert para no duplicar objetos WORM irreversibles bajo la
# carrera síncrono-vs-reconciliación.
# ---------------------------------------------------------------------------


def test_worm_lock_key_is_deterministic_and_in_range() -> None:
    from storage.services.preservation_worm_service import _worm_lock_key

    k1 = _worm_lock_key("icetex", "EXP-1")
    k2 = _worm_lock_key("icetex", "EXP-1")
    assert k1 == k2  # estable (SHA-256, no hash() por-proceso)
    assert _worm_lock_key("icetex", "EXP-2") != k1  # por expediente
    assert _worm_lock_key("otro", "EXP-1") != k1  # por tenant
    assert -(2**63) <= k1 < 2**63  # rango de pg_advisory_lock(bigint)


@pytest.mark.asyncio
async def test_proteger_indice_acquires_and_releases_advisory_lock(client_with_conn) -> None:
    """El happy path toma pg_advisory_lock y lo libera (pg_advisory_unlock) con
    la MISMA clave — serialización por-expediente del check->put->insert."""
    _client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    record = _file_record(file_id)
    worm_row = make_worm_row(expediente_id="EXP-1", object_key=record["object_key"])

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [
        None,  # get_by_expediente_and_tipo
        record,  # get_by_id_any_state
        worm_row,  # insert RETURNING
        {"id": uuid.uuid4()},  # FIJACION ok
        {"id": uuid.uuid4()},  # WORM ok
    ]
    mock_minio.get_object = _minio_get_object(_INDICE_XML)

    response, created = await _proteger(mock_conn, _base_payload(file_id))
    assert created is True

    lock_calls = [
        c for c in mock_conn.execute.call_args_list
        if c.args and "pg_advisory_lock" in c.args[0]
    ]
    unlock_calls = [
        c for c in mock_conn.execute.call_args_list
        if c.args and "pg_advisory_unlock" in c.args[0]
    ]
    assert len(lock_calls) == 1
    assert len(unlock_calls) == 1
    assert lock_calls[0].args[1] == unlock_calls[0].args[1]  # misma clave


@pytest.mark.asyncio
async def test_proteger_indice_releases_lock_on_idempotent_shortcircuit(client_with_conn) -> None:
    """Aun cuando el índice YA está protegido (corte-circuito idempotente, sin
    subir), el lock adquirido se libera en el `finally`."""
    _client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    existing = make_worm_row(expediente_id="EXP-1", object_key="EXP-1/indice.xml")

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [existing]  # get_by_expediente_and_tipo: YA existe

    response, created = await _proteger(mock_conn, _base_payload(file_id))
    assert created is False
    mock_minio.put_object.assert_not_awaited()  # no re-sube

    unlock_calls = [
        c for c in mock_conn.execute.call_args_list
        if c.args and "pg_advisory_unlock" in c.args[0]
    ]
    assert len(unlock_calls) == 1  # liberado pese al corte-circuito
