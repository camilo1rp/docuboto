# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests de POST /api/v1/preservacion/proteger-artefacto[-internal] (E10 Inc.2
wiring) -- generalización de la protección WORM del índice (Increment A) al
acta de transferencia (`tipo='acta_transferencia'`), y del candado DURO que
`/proteger-indice[-internal]` conserva (alias fijo a `tipo='indice'` pase lo
que pase el body).

Espejo de `test_preservacion_worm.py` -- aquí solo la cobertura NUEVA de
Inc.2 wiring: el `tipo` generalizado, la NO-colisión del advisory lock
índice-vs-acta del MISMO expediente, y el alias fijo. Ningún test escribe
objetos COMPLIANCE reales -- MinIO está completamente mockeado
(`tests/conftest.py::_make_mock_minio`); solo se asertan las LLAMADAS.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.core.config as config_module
from app.core.constants import SYSTEM_RECONCILER_ID
from tests.conftest import (
    INTERNAL_HEADERS,
    TENANT_HEADERS,
    TENANT_SLUG,
    make_file_record,
    make_worm_row,
)

_ACTA_XML = b"<acta_fuid>firmada</acta_fuid>"
_ACTA_SHA256 = hashlib.sha256(_ACTA_XML).hexdigest()


@pytest.fixture(autouse=True)
def _preservation_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module.settings, "preservation_enabled", True)


def _grant_permission(mock_conn) -> None:
    mock_conn.fetchval.return_value = True


def _file_record(file_id: uuid.UUID) -> dict:
    return make_file_record(
        file_id=file_id,
        filename="acta-fuid.xml",
        mime_type="application/xml",
        sha256=_ACTA_SHA256,
        object_key=f"{file_id}/acta-fuid.xml",
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
        "tipo": "acta_transferencia",
        "retencion_anios": 5,
        "fecha_inicio": "2026-01-01",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# POST /proteger-artefacto -- tipo='acta_transferencia' (camino nuevo)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proteger_artefacto_happy_path_acta_transferencia(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    record = _file_record(file_id)
    worm_row = make_worm_row(
        expediente_id="EXP-1", tipo="acta_transferencia", object_key=record["object_key"]
    )

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [
        None,  # get_by_expediente_and_tipo(EXP-1, acta_transferencia) -- nada previo
        record,  # FileRepository.get_by_id_any_state
        worm_row,  # insert RETURNING
        {"id": uuid.uuid4()},  # evento FIJACION ok
        {"id": uuid.uuid4()},  # evento WORM ok
    ]
    mock_minio.get_object = _minio_get_object(_ACTA_XML)

    resp = await client.post(
        "/api/v1/preservacion/proteger-artefacto",
        json=_base_payload(file_id),
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["tipo"] == "acta_transferencia"
    assert body["expediente_id"] == "EXP-1"
    # Mismo bucket compartido por tenant que el índice -- el nombre del
    # bucket WORM NO depende del tipo de artefacto.
    assert body["bucket"] == f"orpycamcp-{TENANT_SLUG}-preservation"
    assert body["sha256"] == _ACTA_SHA256

    put_call = mock_minio.put_object.await_args
    assert put_call.args[0] == f"orpycamcp-{TENANT_SLUG}-preservation"
    assert put_call.args[1] == record["object_key"]

    insert_call = [
        c
        for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_worm_objeto" in c.args[0]
    ][0]
    # insert() params: (sql, expediente_id, tipo, bucket, ...)
    assert insert_call.args[1] == "EXP-1"
    assert insert_call.args[2] == "acta_transferencia"


@pytest.mark.asyncio
async def test_proteger_artefacto_second_call_is_idempotent_no_reupload(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    existing_worm_id = uuid.uuid4()
    existing = make_worm_row(
        worm_id=existing_worm_id,
        expediente_id="EXP-1",
        tipo="acta_transferencia",
        object_key="some/acta-fuid.xml",
    )

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [existing]  # get_by_expediente_and_tipo: YA existe

    resp = await client.post(
        "/api/v1/preservacion/proteger-artefacto",
        json=_base_payload(file_id),
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == str(existing_worm_id)
    mock_minio.get_object.assert_not_called()
    mock_minio.put_object.assert_not_awaited()

    insert_calls = [
        c
        for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_worm_objeto" in c.args[0]
    ]
    assert insert_calls == []


@pytest.mark.asyncio
async def test_proteger_artefacto_fixity_mismatch_blocks_upload(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    record = _file_record(file_id)

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [None, record, {"id": uuid.uuid4()}]
    mock_minio.get_object = _minio_get_object(b"acta alterada, no coincide")

    resp = await client.post(
        "/api/v1/preservacion/proteger-artefacto",
        json=_base_payload(file_id),
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "fixity_mismatch"
    mock_minio.put_object.assert_not_awaited()


@pytest.mark.asyncio
async def test_proteger_artefacto_rejects_invalid_tipo(client_with_conn) -> None:
    """El schema `ProtegerIndiceRequest.tipo` es un `Literal["indice",
    "acta_transferencia"]` -- cualquier otro valor (p. ej. 'aip', que SÍ
    admite la columna de BD pero tiene su propio flujo NO-singleton, ver
    `AipCreate`) es rechazado por Pydantic ANTES de tocar BD/MinIO."""
    client, mock_conn, mock_minio = client_with_conn
    _grant_permission(mock_conn)

    resp = await client.post(
        "/api/v1/preservacion/proteger-artefacto",
        json=_base_payload(uuid.uuid4(), tipo="aip"),
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 422
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_proteger_artefacto_default_tipo_is_indice_when_omitted(client_with_conn) -> None:
    """`POST /proteger-artefacto` sin `tipo` en el body usa el default del
    schema ('indice') -- mismo default que protegía la compatibilidad
    histórica ANTES de Inc.2 wiring, ahora también en la ruta generalizada."""
    client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    record = _file_record(file_id)
    worm_row = make_worm_row(expediente_id="EXP-5", tipo="indice", object_key=record["object_key"])

    _grant_permission(mock_conn)
    payload = _base_payload(file_id, expediente_id="EXP-5")
    del payload["tipo"]
    mock_conn.fetchrow.side_effect = [
        None, record, worm_row, {"id": uuid.uuid4()}, {"id": uuid.uuid4()}
    ]
    mock_minio.get_object = _minio_get_object(_ACTA_XML)

    resp = await client.post(
        "/api/v1/preservacion/proteger-artefacto", json=payload, headers=INTERNAL_HEADERS
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["tipo"] == "indice"


# ---------------------------------------------------------------------------
# NO-colisión: índice y acta del MISMO expediente son artefactos
# independientes -- idempotencia por (expediente_id, tipo), no solo por
# expediente_id.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proteger_artefacto_indice_and_acta_same_expediente_do_not_collide(
    client_with_conn,
) -> None:
    """Proteger el índice y luego el acta del MISMO expediente_id -- AMBAS
    llamadas suben y crean su propia fila WORM (201 en ambas), ninguna se
    confunde con el candado idempotente de la otra (hallazgo de auditoría
    Media, `_worm_lock_key` incluye `tipo` desde Inc.2 wiring)."""
    client, mock_conn, mock_minio = client_with_conn
    file_id_indice = uuid.uuid4()
    file_id_acta = uuid.uuid4()
    record_indice = _file_record(file_id_indice)
    record_acta = _file_record(file_id_acta)
    worm_indice = make_worm_row(
        expediente_id="EXP-9", tipo="indice", object_key=record_indice["object_key"]
    )
    worm_acta = make_worm_row(
        expediente_id="EXP-9", tipo="acta_transferencia", object_key=record_acta["object_key"]
    )

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [
        None, record_indice, worm_indice, {"id": uuid.uuid4()}, {"id": uuid.uuid4()},
        None, record_acta, worm_acta, {"id": uuid.uuid4()}, {"id": uuid.uuid4()},
    ]
    mock_minio.get_object = _minio_get_object(_ACTA_XML)

    resp_indice = await client.post(
        "/api/v1/preservacion/proteger-artefacto",
        json=_base_payload(file_id_indice, expediente_id="EXP-9", tipo="indice"),
        headers=INTERNAL_HEADERS,
    )
    resp_acta = await client.post(
        "/api/v1/preservacion/proteger-artefacto",
        json=_base_payload(file_id_acta, expediente_id="EXP-9", tipo="acta_transferencia"),
        headers=INTERNAL_HEADERS,
    )

    assert resp_indice.status_code == 201, resp_indice.text
    assert resp_acta.status_code == 201, resp_acta.text
    assert resp_indice.json()["id"] != resp_acta.json()["id"]

    insert_calls = [
        c
        for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_worm_objeto" in c.args[0]
    ]
    assert len(insert_calls) == 2
    assert {c.args[2] for c in insert_calls} == {"indice", "acta_transferencia"}

    # Dos advisory locks DISTINTOS (uno por tipo) -- nunca el mismo lock_key
    # para el índice y el acta del mismo expediente.
    lock_calls = [
        c for c in mock_conn.execute.call_args_list if c.args and "pg_advisory_lock" in c.args[0]
    ]
    assert len(lock_calls) == 2
    assert lock_calls[0].args[1] != lock_calls[1].args[1]


def test_worm_lock_key_differs_by_tipo_for_same_tenant_and_expediente() -> None:
    from app.services.preservation_worm_service import _worm_lock_key

    k_indice = _worm_lock_key("icetex", "EXP-1", "indice")
    k_acta = _worm_lock_key("icetex", "EXP-1", "acta_transferencia")
    assert k_indice != k_acta
    # Determinista y estable frente a repetición (mismo criterio que la
    # cobertura existente del índice).
    assert _worm_lock_key("icetex", "EXP-1", "acta_transferencia") == k_acta


# ---------------------------------------------------------------------------
# POST /proteger-artefacto-internal (D-02, SYSTEM_RECONCILER_ID) -- 4ª fase
# de reconciliación (`run_once_actas_worm`, archive-service).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proteger_artefacto_internal_happy_path_uses_system_actor(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    record = _file_record(file_id)
    worm_row = make_worm_row(
        expediente_id="EXP-3", tipo="acta_transferencia", object_key=record["object_key"]
    )

    mock_conn.fetchrow.side_effect = [
        None, record, worm_row, {"id": uuid.uuid4()}, {"id": uuid.uuid4()}
    ]
    mock_minio.get_object = _minio_get_object(_ACTA_XML)

    resp = await client.post(
        "/api/v1/preservacion/proteger-artefacto-internal",
        json=_base_payload(file_id, expediente_id="EXP-3"),
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    audit_inserts = [
        c
        for c in mock_conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_inserts) == 1
    assert audit_inserts[0].args[3] == str(SYSTEM_RECONCILER_ID)


@pytest.mark.asyncio
async def test_proteger_artefacto_internal_rejects_without_token(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn

    resp = await client.post(
        "/api/v1/preservacion/proteger-artefacto-internal",
        json=_base_payload(uuid.uuid4()),
        headers=TENANT_HEADERS,  # sin X-Internal-Token
    )

    assert resp.status_code == 401
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_proteger_artefacto_internal_second_call_is_idempotent(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    existing = make_worm_row(expediente_id="EXP-4", tipo="acta_transferencia")

    mock_conn.fetchrow.side_effect = [existing]

    resp = await client.post(
        "/api/v1/preservacion/proteger-artefacto-internal",
        json=_base_payload(uuid.uuid4(), expediente_id="EXP-4"),
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 200, resp.text
    mock_minio.put_object.assert_not_awaited()


# ---------------------------------------------------------------------------
# Alias DURO: /proteger-indice[-internal] SIEMPRE fuerza tipo='indice',
# pase lo que pase el body (E10 Inc.2 wiring) -- `model_copy(update={"tipo":
# "indice"})` en el router, verificado end-to-end.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proteger_indice_alias_forces_tipo_indice_even_if_body_declares_acta(
    client_with_conn,
) -> None:
    client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    record = _file_record(file_id)
    worm_row = make_worm_row(expediente_id="EXP-7", tipo="indice", object_key=record["object_key"])

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [
        None, record, worm_row, {"id": uuid.uuid4()}, {"id": uuid.uuid4()}
    ]
    mock_minio.get_object = _minio_get_object(_ACTA_XML)

    resp = await client.post(
        "/api/v1/preservacion/proteger-indice",
        # El body pide 'acta_transferencia' -- el router lo pisa igual.
        json=_base_payload(file_id, expediente_id="EXP-7", tipo="acta_transferencia"),
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["tipo"] == "indice"

    insert_call = [
        c
        for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_worm_objeto" in c.args[0]
    ][0]
    assert insert_call.args[2] == "indice"


@pytest.mark.asyncio
async def test_proteger_indice_internal_alias_forces_tipo_indice_even_if_body_declares_acta(
    client_with_conn,
) -> None:
    client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    record = _file_record(file_id)
    worm_row = make_worm_row(expediente_id="EXP-8", tipo="indice", object_key=record["object_key"])

    mock_conn.fetchrow.side_effect = [
        None, record, worm_row, {"id": uuid.uuid4()}, {"id": uuid.uuid4()}
    ]
    mock_minio.get_object = _minio_get_object(_ACTA_XML)

    resp = await client.post(
        "/api/v1/preservacion/proteger-indice-internal",
        json=_base_payload(file_id, expediente_id="EXP-8", tipo="acta_transferencia"),
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["tipo"] == "indice"


# ---------------------------------------------------------------------------
# Migración 010 (CHECK constraint ampliado + índice único parcial del acta)
# -- chequeo ESTÁTICO del SQL, mismo patrón que
# `test_aip_pdfa.py::test_migration_008_check_constraint_admits_no_evaluado`:
# storage-service no tiene tier de integración contra Postgres real (el run
# mandatado usa `--no-deps`), así que esto es lo más cerca que este
# repositorio llega de validar la migración sin levantar infraestructura
# nueva. Ver el hueco de cobertura declarado en el informe de esta tarea.
# ---------------------------------------------------------------------------

_MIGRATION_010_PATH = (
    Path(__file__).resolve().parent.parent / "migrations" / "tenant" / "010_preservacion_worm_acta.sql"
)


def test_migration_010_check_constraint_admits_acta_transferencia() -> None:
    sql = _MIGRATION_010_PATH.read_text(encoding="utf-8")

    check_match = re.search(r"CHECK\s*\(tipo IN \(([^)]+)\)\)", sql)
    assert check_match, "no se encontró el CHECK constraint de tipo en la migración 010"
    allowed_values = {v.strip().strip("'") for v in check_match.group(1).split(",")}
    assert allowed_values == {"indice", "aip", "acta_transferencia"}

    # Índice único parcial de idempotencia del acta -- espejo EXACTO del
    # índice (migración 009).
    assert "ux_preservacion_worm_objeto_expediente_acta" in sql
    assert "WHERE tipo = 'acta_transferencia'" in sql


def test_migration_010_ddl_is_idempotent_by_construction() -> None:
    """La migración es segura de re-ejecutar sobre un schema ya migrado:
    `DROP CONSTRAINT IF EXISTS` + `ADD CONSTRAINT` (el CHECK en sí no admite
    `IF NOT EXISTS`, de ahí el drop-then-add) y `CREATE ... INDEX IF NOT
    EXISTS` -- ninguna sentencia asume estado previo sin comprobarlo."""
    sql = _MIGRATION_010_PATH.read_text(encoding="utf-8")

    assert "DROP CONSTRAINT IF EXISTS preservacion_worm_objeto_tipo_check" in sql
    assert "ADD CONSTRAINT preservacion_worm_objeto_tipo_check" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS ux_preservacion_worm_objeto_expediente_acta" in sql
    # Ninguna sentencia `CREATE TABLE`/`DROP TABLE` -- puramente aditiva
    # sobre la tabla ya provista por la migración 006.
    assert "CREATE TABLE" not in sql
    assert "DROP TABLE" not in sql


# ---------------------------------------------------------------------------
# AREA-07-10 -- las firmas PERSONALES del acta entran al almacén WORM (un tipo por rol)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("tipo", ["acta_firma_elabora", "acta_firma_entrega", "acta_firma_recibe"])
async def test_proteger_artefacto_firma_personal_por_rol(client_with_conn, tipo: str) -> None:
    client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    record = _file_record(file_id)
    worm_row = make_worm_row(expediente_id="EXP-1", tipo=tipo, object_key=record["object_key"])

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [
        None,  # get_by_expediente_and_tipo(EXP-1, tipo) -- nada previo
        record,
        worm_row,
        {"id": uuid.uuid4()},
        {"id": uuid.uuid4()},
    ]
    mock_minio.get_object = _minio_get_object(_ACTA_XML)

    resp = await client.post(
        "/api/v1/preservacion/proteger-artefacto",
        json=_base_payload(file_id, tipo=tipo),
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["tipo"] == tipo
    insert_call = next(
        c for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_worm_objeto" in c.args[0]
    )
    assert insert_call.args[2] == tipo
    mock_minio.put_object.assert_awaited_once()


def test_las_firmas_personales_son_instrumentos_de_control_y_estan_en_la_migracion() -> None:
    """No postergan la disposición del contenido (como el acta que firman) y el CHECK de la
    migración 018 los admite: el guard `test_preservacion_tipos_guard` ata las dos listas."""
    from app.core.preservacion_tipos import CONTROL_INSTRUMENT_TIPOS

    for t in ("acta_firma_elabora", "acta_firma_entrega", "acta_firma_recibe"):
        assert t in CONTROL_INSTRUMENT_TIPOS


# ---------------------------------------------------------------------------
# AREA-07-10 -- la constancia de «firma inobtenible» (sello y firma personal del declarante)
# ---------------------------------------------------------------------------

_TIPOS_CONSTANCIA = [
    f"acta_firma_excepcion_{variante}{rol}"
    for variante in ("", "personal_", "sobre_")
    for rol in ("elabora", "entrega", "recibe")
]


@pytest.mark.asyncio
@pytest.mark.parametrize("tipo", _TIPOS_CONSTANCIA)
async def test_proteger_artefacto_constancia_firma_inobtenible_por_rol(
    client_with_conn, tipo: str
) -> None:
    client, mock_conn, mock_minio = client_with_conn
    file_id = uuid.uuid4()
    record = _file_record(file_id)
    worm_row = make_worm_row(expediente_id="EXP-1", tipo=tipo, object_key=record["object_key"])

    _grant_permission(mock_conn)
    mock_conn.fetchrow.side_effect = [
        None,
        record,
        worm_row,
        {"id": uuid.uuid4()},
        {"id": uuid.uuid4()},
    ]
    mock_minio.get_object = _minio_get_object(_ACTA_XML)

    resp = await client.post(
        "/api/v1/preservacion/proteger-artefacto",
        json=_base_payload(file_id, tipo=tipo),
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["tipo"] == tipo
    mock_minio.put_object.assert_awaited_once()


def test_las_constancias_son_instrumentos_de_control() -> None:
    from app.core.preservacion_tipos import CONTROL_INSTRUMENT_TIPOS

    for t in _TIPOS_CONSTANCIA:
        assert t in CONTROL_INSTRUMENT_TIPOS
