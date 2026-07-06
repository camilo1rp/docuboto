# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests de POST /api/v1/preservacion/proteger-indice (E10 Increment A, RF-PRE-03).

Ningún test escribe objetos COMPLIANCE reales — MinIO está completamente mockeado
(`tests/conftest.py::_make_mock_minio`); solo se asertan las LLAMADAS (make_bucket,
put_object, get_object_lock_config, etc.) y sus argumentos.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from miniopy_async import S3Error  # type: ignore[attr-defined]

import app.core.config as config_module
from tests.conftest import TENANT_HEADERS, TENANT_SLUG, make_file_record, make_worm_row

_INDICE_XML = b"<indice>firmado</indice>"
_INDICE_SHA256 = hashlib.sha256(_INDICE_XML).hexdigest()


@pytest.fixture(autouse=True)
def _preservation_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """PRESERVATION_ENABLED=false es el default seguro (kill-switch) — se activa
    aquí para poder ejercer el endpoint; `test_proteger_indice_disabled_by_feature_flag`
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
        "legal_hold": False,
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_proteger_indice_happy_path(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    record = _file_record(file_id)
    worm_row = make_worm_row(expediente_id="EXP-1", object_key=record["object_key"])

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [
        record,  # FileRepository.get_by_id_any_state
        worm_row,  # PreservacionWormRepository.insert (RETURNING)
        {"id": uuid.uuid4()},  # insert_evento FIJACION ok
        {"id": uuid.uuid4()},  # insert_evento WORM ok
    ]
    mock_minio.get_object = _minio_get_object(_INDICE_XML)

    resp = await client.post(
        "/api/v1/preservacion/proteger-indice",
        json=_base_payload(file_id),
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
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
    client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    record = _file_record(file_id)
    worm_row = make_worm_row(expediente_id="EXP-1", object_key=record["object_key"])

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [
        record,
        worm_row,
        {"id": uuid.uuid4()},
        {"id": uuid.uuid4()},
    ]
    mock_minio.get_object = _minio_get_object(_INDICE_XML)

    direct_until = "2032-06-15T00:00:00Z"
    resp = await client.post(
        "/api/v1/preservacion/proteger-indice",
        json=_base_payload(
            file_id, retencion_anios=None, fecha_inicio=None, retain_until=direct_until
        ),
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    put_call = mock_minio.put_object.await_args
    retention = put_call.kwargs["retention"]
    assert retention.retain_until_date == datetime(2032, 6, 15, tzinfo=UTC)


@pytest.mark.asyncio
async def test_proteger_indice_legal_hold_true_reaches_minio_and_audit(client_with_conn) -> None:
    """`legal_hold=True` se propaga al `put_object` real y queda en el payload de
    auditoría — es lo único, junto a `retain_until` futuro, que bloquea la purga
    (ver `test_purge_worm_gate.py::test_purge_blocked_by_legal_hold_even_if_retain_until_past`)."""
    client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    record = _file_record(file_id)
    worm_row = make_worm_row(expediente_id="EXP-1", object_key=record["object_key"], legal_hold=True)

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [record, worm_row, {"id": uuid.uuid4()}, {"id": uuid.uuid4()}]
    mock_minio.get_object = _minio_get_object(_INDICE_XML)

    resp = await client.post(
        "/api/v1/preservacion/proteger-indice",
        json=_base_payload(file_id, legal_hold=True),
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["legal_hold"] is True
    put_call = mock_minio.put_object.await_args
    assert put_call.kwargs["legal_hold"] is True

    audit_inserts = [
        c
        for c in mock_conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]
    audit_payload = json.loads(audit_inserts[0].args[8])
    assert audit_payload["legal_hold"] is True


@pytest.mark.asyncio
async def test_proteger_indice_rejects_retain_until_beyond_max_years(client_with_conn) -> None:
    """`retain_until` futuro pero por encima del techo PRESERVATION_MAX_RETENTION_YEARS
    (100 años) — probable bug de unidades del llamador, no una TRD real."""
    client, mock_conn, mock_minio = client_with_conn
    _grant_permission(mock_conn)
    too_far = (datetime.now(tz=UTC) + timedelta(days=365 * 200)).isoformat()

    resp = await client.post(
        "/api/v1/preservacion/proteger-indice",
        json=_base_payload(
            uuid.uuid4(), retencion_anios=None, fecha_inicio=None, retain_until=too_far
        ),
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_retention"
    mock_conn.fetchrow.assert_not_called()  # falla antes de tocar BD/MinIO
    mock_minio.put_object.assert_not_awaited()


@pytest.mark.asyncio
async def test_proteger_indice_provisions_bucket_when_absent(client_with_conn) -> None:
    """Bucket ausente → se crea con object_lock=True (PIEZA 2 caso a)."""
    client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    record = _file_record(file_id)
    worm_row = make_worm_row(object_key=record["object_key"])

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [record, worm_row, {"id": uuid.uuid4()}, {"id": uuid.uuid4()}]
    mock_minio.get_object = _minio_get_object(_INDICE_XML)
    mock_minio.bucket_exists = AsyncMock(return_value=False)

    resp = await client.post(
        "/api/v1/preservacion/proteger-indice",
        json=_base_payload(file_id),
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    mock_minio.make_bucket.assert_awaited_once_with(
        f"orpycamcp-{TENANT_SLUG}-preservation", object_lock=True
    )
    mock_minio.get_object_lock_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_proteger_indice_bucket_exists_without_lock_is_rejected(client_with_conn) -> None:
    """Bucket existente SIN Object Lock → error ruidoso, se aborta (PIEZA 2 caso c)."""
    client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    record = _file_record(file_id)

    _grant_permission(mock_conn)
    mock_conn.fetchrow.return_value = record
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

    resp = await client.post(
        "/api/v1/preservacion/proteger-indice",
        json=_base_payload(file_id),
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 500
    assert resp.json()["detail"]["error"] == "worm_bucket_misconfigured"
    mock_minio.put_object.assert_not_awaited()


@pytest.mark.asyncio
async def test_proteger_indice_fixity_mismatch_blocks_upload(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    record = _file_record(file_id)

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [record, {"id": uuid.uuid4()}]  # file + evento FIJACION fallo
    mock_minio.get_object = _minio_get_object(b"contenido alterado, no coincide")

    resp = await client.post(
        "/api/v1/preservacion/proteger-indice",
        json=_base_payload(file_id),
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "fixity_mismatch"
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
    client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    record = _file_record(file_id)
    worm_row = make_worm_row(expediente_id="EXP  RARO", object_key=record["object_key"])

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [
        record,
        worm_row,
        {"id": uuid.uuid4()},
        {"id": uuid.uuid4()},
    ]
    mock_minio.get_object = _minio_get_object(_INDICE_XML)

    resp = await client.post(
        "/api/v1/preservacion/proteger-indice",
        json=_base_payload(file_id, expediente_id="  EXP   RARO  "),
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["expediente_id"] == "EXP RARO"

    insert_call = [
        c
        for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_worm_objeto" in c.args[0]
    ][0]
    # insert() params: (sql, expediente_id, tipo, bucket, ...)
    assert insert_call.args[1] == "EXP RARO"


@pytest.mark.asyncio
async def test_proteger_indice_rejects_blank_expediente_id(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    _grant_permission(mock_conn)

    resp = await client.post(
        "/api/v1/preservacion/proteger-indice",
        json=_base_payload(uuid.uuid4(), expediente_id="   "),
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_proteger_indice_file_not_found(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    _grant_permission(mock_conn)
    mock_conn.fetchrow.return_value = None

    resp = await client.post(
        "/api/v1/preservacion/proteger-indice",
        json=_base_payload(uuid.uuid4()),
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "file_not_found"


@pytest.mark.asyncio
async def test_proteger_indice_rejects_past_retain_until(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    _grant_permission(mock_conn)

    resp = await client.post(
        "/api/v1/preservacion/proteger-indice",
        json=_base_payload(
            uuid.uuid4(),
            retencion_anios=None,
            fecha_inicio=None,
            retain_until="2020-01-01T00:00:00Z",
        ),
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_retention"
    mock_conn.fetchrow.assert_not_called()  # falla antes de tocar BD/MinIO


@pytest.mark.asyncio
async def test_proteger_indice_rejects_retention_below_floor(client_with_conn) -> None:
    """retain_until futuro pero por debajo del piso PRESERVATION_MIN_RETENTION_DAYS (365)."""
    client, mock_conn, mock_minio = client_with_conn
    _grant_permission(mock_conn)
    soon = (datetime.now(tz=UTC) + timedelta(days=5)).isoformat()

    resp = await client.post(
        "/api/v1/preservacion/proteger-indice",
        json=_base_payload(
            uuid.uuid4(), retencion_anios=None, fecha_inicio=None, retain_until=soon
        ),
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_retention"


@pytest.mark.asyncio
async def test_proteger_indice_bucket_name_derived_from_claim_not_body(client_with_conn) -> None:
    """El nombre del bucket WORM sale de X-Tenant-Slug (claim), nunca del body —
    un `tenant_slug` colado en el body es un campo desconocido para el schema y
    se ignora (Pydantic v2 `extra="ignore"` por defecto)."""
    client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    record = _file_record(file_id)
    worm_row = make_worm_row(object_key=record["object_key"])

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [record, worm_row, {"id": uuid.uuid4()}, {"id": uuid.uuid4()}]
    mock_minio.get_object = _minio_get_object(_INDICE_XML)

    payload = _base_payload(file_id)
    payload["tenant_slug"] = "attacker-tenant"  # ignorado: no es un campo del schema

    resp = await client.post(
        "/api/v1/preservacion/proteger-indice",
        json=payload,
        headers=TENANT_HEADERS,  # X-Tenant-Slug: demo
    )

    assert resp.status_code == 201, resp.text
    put_call = mock_minio.put_object.await_args
    assert put_call.args[0] == f"orpycamcp-{TENANT_SLUG}-preservation"
    assert "attacker" not in put_call.args[0]


@pytest.mark.asyncio
async def test_proteger_indice_disabled_by_feature_flag(client_with_conn, monkeypatch) -> None:
    client, mock_conn, mock_minio = client_with_conn
    _grant_permission(mock_conn)
    monkeypatch.setattr(config_module.settings, "preservation_enabled", False)

    resp = await client.post(
        "/api/v1/preservacion/proteger-indice",
        json=_base_payload(uuid.uuid4()),
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "preservation_disabled"
    mock_conn.fetchrow.assert_not_called()
