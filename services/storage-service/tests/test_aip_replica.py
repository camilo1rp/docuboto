# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests de la réplica best-effort del AIP (E10 Increment C1b, RF-FIR-15).

Ningún test escribe objetos COMPLIANCE reales — MinIO está completamente
mockeado (`tests/conftest.py::_make_mock_minio`); solo se asertan las
LLAMADAS (make_bucket, copy_object, put_object) y los datos que se les pasan.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.core.config as config_module
import app.services.aip_replica_service as aip_replica_module
from tests.conftest import INTERNAL_HEADERS, TENANT_SLUG, make_worm_row, minio_get_object

_DOC_CONTENT = b"contenido real del oficio radicado"
_DOC_SHA256 = hashlib.sha256(_DOC_CONTENT).hexdigest()
_PRIMARY_BUCKET = f"orpycamcp-{TENANT_SLUG}-preservation"
_REPLICA_BUCKET = f"orpycamcp-{TENANT_SLUG}-preservation-replica"


@pytest.fixture(autouse=True)
def _preservation_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module.settings, "preservation_enabled", True)


def _grant_permission(mock_conn, *, num_copias) -> None:
    """(AREA-08-16: la ruta ya no pasa por `require_permission`, solo por el token
    interno, así que ya no hay `fetchval` de permiso.) 1.º fetchval = num_copias
    del plan vigente (`PreservacionRepository.current_num_copias`, vía
    ``fetchval`` — deliberadamente NO consume la lista de ``fetchrow``, ver
    docstring del método); 2.º = `prev_hash` de `audit.append` (cadena de
    hash del audit_log — `None` = sin registro previo, válido)."""
    mock_conn.fetchval.side_effect = [num_copias, None]


def _doc_record(file_id: uuid.UUID) -> dict:
    from tests.conftest import make_file_record

    return make_file_record(
        file_id=file_id,
        filename="oficio.pdf",
        mime_type="application/pdf",
        sha256=_DOC_SHA256,
        object_key=f"{file_id}/oficio.pdf",
    )


def _base_payload(*, doc_file_id: uuid.UUID) -> dict:
    return {
        "expediente_id": "EXP-1",
        "documentos": [
            {"document_id": "R-1", "nombre": "oficio.pdf", "file_id": str(doc_file_id)}
        ],
        "retencion_anios": 5,
        "fecha_inicio": "2026-01-01",
    }


def _aip_fetchrow_sequence(*, doc_file_id: uuid.UUID, aip_id: uuid.UUID, retain_until: datetime) -> list:
    """Secuencia completa de `fetchrow` para un empaquetado con UN documento,
    sin índice (doc + insert_aip + worm_insert + FIJACION/WORM/INGESTA)."""
    return [
        _doc_record(doc_file_id),
        None,  # get_aip_by_expediente_and_sha256 (idempotencia: sin duplicado previo)
        {
            "id": aip_id,
            "expediente_id": "EXP-1",
            "premis_xml": "<premis/>",
            "aip_sha256": "c" * 64,
            "indice_sha256": None,
            "num_documentos": 1,
            "estado": "empaquetado",
            "created_at": datetime.now(tz=UTC),
            "bucket": _PRIMARY_BUCKET,
            "object_key": f"aip/EXP-1/x-{aip_id}.zip",
            "aip_bytes": 999,
            "formato": "application/zip",
            "retain_until": retain_until,
            "retention_mode": "COMPLIANCE",
            "legal_hold": False,
        },
        make_worm_row(expediente_id="EXP-1", tipo="aip", retain_until=retain_until),
        {
            "id": uuid.uuid4(), "object_ref": "EXP-1", "tipo": "FIJACION", "resultado": "ok",
            "hash_before": None, "hash_after": "c" * 64, "detalle": None, "agente": None,
            "ts": datetime.now(tz=UTC),
        },
        {
            "id": uuid.uuid4(), "object_ref": "EXP-1", "tipo": "WORM", "resultado": "ok",
            "hash_before": None, "hash_after": "c" * 64, "detalle": None, "agente": None,
            "ts": datetime.now(tz=UTC),
        },
        {
            "id": uuid.uuid4(), "object_ref": "EXP-1", "tipo": "INGESTA", "resultado": "ok",
            "hash_before": None, "hash_after": "c" * 64, "detalle": None, "agente": None,
            "ts": datetime.now(tz=UTC),
        },
    ]


def _replica_evento_row(*, resultado: str) -> dict:
    return {
        "id": uuid.uuid4(), "object_ref": "EXP-1", "tipo": "REPLICA", "resultado": resultado,
        "hash_before": None, "hash_after": "c" * 64 if resultado == "ok" else None,
        "detalle": None, "agente": None, "ts": datetime.now(tz=UTC),
    }


def _only_replica_bucket_missing(bucket: str) -> bool:
    """El bucket réplica NO existe todavía (fuerza `make_bucket`); el primario
    (ya provisionado por `proteger_indice`/un AIP anterior) SÍ existe."""
    return bucket != _REPLICA_BUCKET


def _minio_get_con_replica(
    mock_minio, *, bytes_de_la_replica: bytes | None = None
) -> AsyncMock:
    """`get_object` del almacén: el documento en su bucket y, para el bucket réplica,
    los bytes del ZIP que el primario acaba de subir (un almacén que COPIA bien) o los
    que se indiquen (un almacén que corrompe la copia)."""

    async def _get(bucket, _key, *_a, **_k):
        if bucket == _REPLICA_BUCKET:
            contenido = bytes_de_la_replica
            if contenido is None:
                contenido = mock_minio.put_object.await_args_list[0].args[2].getvalue()
        else:
            contenido = _DOC_CONTENT
        obj = MagicMock()
        obj.read = AsyncMock(return_value=contenido)
        obj.close = MagicMock()
        return obj

    return AsyncMock(side_effect=_get)


def _make_mock_secondary_minio() -> AsyncMock:
    """Cliente MinIO del SEGUNDO endpoint (réplica cross-site).

    Nunca es el mismo objeto que `mock_minio` (el primario): apunta a un host
    distinto, así que se le inyecta un mock propio vía
    `aip_replica_module._get_secondary_minio` — usar `copy_object` aquí sería
    incorrecto (no existe copia server-side entre endpoints MinIO distintos,
    ver docstring del módulo), por eso NO trae ese método configurado.
    """
    minio = AsyncMock()
    minio.bucket_exists = AsyncMock(return_value=False)  # bucket réplica aún no existe
    minio.make_bucket = AsyncMock()
    minio.put_object = AsyncMock(return_value=AsyncMock(version_id="v1"))
    minio._ultima_retencion = None

    def _recordar(*_a, **kw):
        minio._ultima_retencion = kw.get("retention")
        return MagicMock(version_id="v1")

    minio.put_object = AsyncMock(side_effect=_recordar)

    async def _retencion(*_a, **_k):
        return minio._ultima_retencion

    minio.get_object_retention = AsyncMock(side_effect=_retencion)
    return minio


@pytest.mark.asyncio
async def test_replica_disabled_by_default_no_replica_attempted(client_with_conn) -> None:
    """`PRESERVATION_REPLICA_ENABLED=false` (default) -> comportamiento
    IDÉNTICO al AIP sin réplica: sin evento REPLICA, sin copy_object."""
    client, mock_conn, mock_minio = client_with_conn
    doc_file_id = uuid.uuid4()
    aip_id = uuid.uuid4()
    retain_until = datetime(2031, 1, 1, tzinfo=UTC)

    _grant_permission(mock_conn, num_copias=2)  # aunque el plan pida 2 copias...
    mock_conn.fetchrow.side_effect = _aip_fetchrow_sequence(
        doc_file_id=doc_file_id, aip_id=aip_id, retain_until=retain_until
    )
    mock_minio.get_object = minio_get_object(_DOC_CONTENT)

    resp = await client.post(
        "/api/v1/preservacion/aip",
        json=_base_payload(doc_file_id=doc_file_id),
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["replicado"] is False
    assert body["replica_target"] is None
    assert body["replica_copias_hechas"] == 1

    mock_minio.copy_object.assert_not_called()
    mock_minio.put_object.assert_awaited_once()  # solo la subida primaria

    evento_tipos = [
        c.args[2] for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_evento" in c.args[0]
    ]
    assert "REPLICA" not in evento_tipos


@pytest.mark.asyncio
async def test_replica_num_copias_uno_no_replica(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`num_copias<=1` (copia única declarada por el plan) -> CERO réplicas,
    aunque el feature flag esté encendido."""
    client, mock_conn, mock_minio = client_with_conn
    doc_file_id = uuid.uuid4()
    aip_id = uuid.uuid4()
    retain_until = datetime(2031, 1, 1, tzinfo=UTC)

    monkeypatch.setattr(config_module.settings, "preservation_replica_enabled", True)
    _grant_permission(mock_conn, num_copias=1)
    mock_conn.fetchrow.side_effect = _aip_fetchrow_sequence(
        doc_file_id=doc_file_id, aip_id=aip_id, retain_until=retain_until
    )
    mock_minio.get_object = minio_get_object(_DOC_CONTENT)

    resp = await client.post(
        "/api/v1/preservacion/aip",
        json=_base_payload(doc_file_id=doc_file_id),
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["replicado"] is False
    assert body["replica_copias_hechas"] == 1
    mock_minio.copy_object.assert_not_called()


@pytest.mark.asyncio
async def test_replica_local_success_provisions_bucket_and_copies(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`num_copias=2` + réplica habilitada, SIN `MINIO_SECONDARY_ENDPOINT` ->
    réplica LOCAL: provisiona el bucket réplica (`make_bucket(object_lock=True)`)
    y copia el ZIP server-side (`copy_object`) con la MISMA retención."""
    client, mock_conn, mock_minio = client_with_conn
    doc_file_id = uuid.uuid4()
    aip_id = uuid.uuid4()
    retain_until = datetime(2031, 1, 1, tzinfo=UTC)

    monkeypatch.setattr(config_module.settings, "preservation_replica_enabled", True)
    _grant_permission(mock_conn, num_copias=2)
    mock_conn.fetchrow.side_effect = _aip_fetchrow_sequence(
        doc_file_id=doc_file_id, aip_id=aip_id, retain_until=retain_until
    ) + [_replica_evento_row(resultado="ok")]
    mock_minio.get_object = _minio_get_con_replica(mock_minio)
    mock_minio.bucket_exists = AsyncMock(side_effect=_only_replica_bucket_missing)

    resp = await client.post(
        "/api/v1/preservacion/aip",
        json=_base_payload(doc_file_id=doc_file_id),
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["replicado"] is True
    assert body["replica_target"] == "local"
    assert body["replica_copias_hechas"] == 2

    # --- bucket réplica provisionado con Object Lock ---
    make_bucket_call = mock_minio.make_bucket.await_args
    assert make_bucket_call.args[0] == _REPLICA_BUCKET
    assert make_bucket_call.kwargs["object_lock"] is True

    # --- copy_object: mismo object_key que el primario, MISMA retención ---
    put_call = mock_minio.put_object.await_args
    primary_bucket, object_key = put_call.args[0], put_call.args[1]
    assert primary_bucket == _PRIMARY_BUCKET

    copy_call = mock_minio.copy_object.await_args
    assert copy_call.args[0] == _REPLICA_BUCKET
    assert copy_call.args[1] == object_key
    source = copy_call.args[2]
    assert source.bucket_name == primary_bucket
    assert source.object_name == object_key
    assert copy_call.kwargs["retention"].mode == "COMPLIANCE"
    assert copy_call.kwargs["retention"].retain_until_date == retain_until
    assert copy_call.kwargs["legal_hold"] is False

    # --- evento REPLICA ok, etiquetado HONESTAMENTE como local ---
    replica_evt = [
        c for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_evento" in c.args[0] and c.args[2] == "REPLICA"
    ]
    assert len(replica_evt) == 1
    assert replica_evt[0].args[3] == "ok"
    import json as _json

    detalle = _json.loads(replica_evt[0].args[6])
    assert detalle["target"] == "local"
    assert "NO es copia geográfica" in detalle["nota"]
    assert detalle["copias_solicitadas"] == 2
    assert detalle["copias_hechas"] == 2


@pytest.mark.asyncio
async def test_replica_num_copias_mayor_a_dos_registra_deficit_honesto(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`num_copias=3` con solo un target de réplica disponible (C1b) -> UNA
    réplica se hace igual, pero el evento registra el déficit
    (`copias_solicitadas=3` vs `copias_hechas=2`) en vez de simular 3 copias."""
    client, mock_conn, mock_minio = client_with_conn
    doc_file_id = uuid.uuid4()
    aip_id = uuid.uuid4()
    retain_until = datetime(2031, 1, 1, tzinfo=UTC)

    monkeypatch.setattr(config_module.settings, "preservation_replica_enabled", True)
    _grant_permission(mock_conn, num_copias=3)
    mock_conn.fetchrow.side_effect = _aip_fetchrow_sequence(
        doc_file_id=doc_file_id, aip_id=aip_id, retain_until=retain_until
    ) + [_replica_evento_row(resultado="ok")]
    mock_minio.get_object = _minio_get_con_replica(mock_minio)
    mock_minio.bucket_exists = AsyncMock(side_effect=_only_replica_bucket_missing)

    resp = await client.post(
        "/api/v1/preservacion/aip",
        json=_base_payload(doc_file_id=doc_file_id),
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["replicado"] is True
    assert body["replica_copias_hechas"] == 2  # NO 3 -- déficit honesto

    replica_evt = [
        c for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_evento" in c.args[0] and c.args[2] == "REPLICA"
    ][0]
    import json as _json

    detalle = _json.loads(replica_evt.args[6])
    assert detalle["copias_solicitadas"] == 3
    assert detalle["copias_hechas"] == 2


@pytest.mark.asyncio
async def test_replica_fallo_es_best_effort_no_fatal(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un fallo de I/O al copiar la réplica NO revierte el AIP primario (ya
    sellado + WORM): la fila `aip`, `preservacion_worm_objeto` y los eventos
    FIJACION/WORM/INGESTA quedan comitteados igual; solo el evento REPLICA
    queda con resultado='fallo'."""
    client, mock_conn, mock_minio = client_with_conn
    doc_file_id = uuid.uuid4()
    aip_id = uuid.uuid4()
    retain_until = datetime(2031, 1, 1, tzinfo=UTC)

    monkeypatch.setattr(config_module.settings, "preservation_replica_enabled", True)
    _grant_permission(mock_conn, num_copias=2)
    mock_conn.fetchrow.side_effect = _aip_fetchrow_sequence(
        doc_file_id=doc_file_id, aip_id=aip_id, retain_until=retain_until
    ) + [_replica_evento_row(resultado="fallo")]
    mock_minio.get_object = _minio_get_con_replica(mock_minio)
    mock_minio.bucket_exists = AsyncMock(side_effect=_only_replica_bucket_missing)
    mock_minio.copy_object = AsyncMock(side_effect=RuntimeError("MinIO replica endpoint unreachable"))

    resp = await client.post(
        "/api/v1/preservacion/aip",
        json=_base_payload(doc_file_id=doc_file_id),
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 201, resp.text  # el AIP primario NO se ve afectado
    body = resp.json()
    assert body["replicado"] is False
    assert body["replica_copias_hechas"] == 1

    # --- el AIP primario sigue comitteado: aip + worm_objeto + 3 eventos ok ---
    insert_calls = [c.args[0] for c in mock_conn.fetchrow.call_args_list if c.args]
    assert any("INSERT INTO aip" in sql for sql in insert_calls)
    assert any("INSERT INTO preservacion_worm_objeto" in sql for sql in insert_calls)

    ok_eventos = [
        c.args[2] for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_evento" in c.args[0] and c.args[3] == "ok"
    ]
    assert set(ok_eventos) == {"FIJACION", "WORM", "INGESTA"}

    replica_evt = [
        c for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_evento" in c.args[0] and c.args[2] == "REPLICA"
    ]
    assert len(replica_evt) == 1
    assert replica_evt[0].args[3] == "fallo"
    import json as _json

    detalle = _json.loads(replica_evt[0].args[6])
    # AREA-08-04: solo el TIPO de la excepción, nunca su texto (host/bucket/request-id).
    assert detalle["motivo_fallo"] == "RuntimeError"
    assert "unreachable" not in detalle["motivo_fallo"]


@pytest.mark.asyncio
async def test_replica_bucket_derivado_del_claim_no_del_body(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El bucket réplica se deriva SIEMPRE de `X-Tenant-Slug` (el mismo claim
    que usa el bucket primario) -- ni `AipCreate` declara un campo tenant, ni
    un valor colado en el body tiene efecto (Pydantic v2 ignora extras)."""
    client, mock_conn, mock_minio = client_with_conn
    doc_file_id = uuid.uuid4()
    aip_id = uuid.uuid4()
    retain_until = datetime(2031, 1, 1, tzinfo=UTC)

    monkeypatch.setattr(config_module.settings, "preservation_replica_enabled", True)
    _grant_permission(mock_conn, num_copias=2)
    mock_conn.fetchrow.side_effect = _aip_fetchrow_sequence(
        doc_file_id=doc_file_id, aip_id=aip_id, retain_until=retain_until
    ) + [_replica_evento_row(resultado="ok")]
    mock_minio.get_object = _minio_get_con_replica(mock_minio)
    mock_minio.bucket_exists = AsyncMock(side_effect=_only_replica_bucket_missing)

    payload = _base_payload(doc_file_id=doc_file_id)
    payload["tenant_slug"] = "attacker-tenant"  # campo no declarado -- debe ser ignorado

    resp = await client.post(
        "/api/v1/preservacion/aip", json=payload, headers=INTERNAL_HEADERS
    )

    assert resp.status_code == 201, resp.text
    make_bucket_call = mock_minio.make_bucket.await_args
    assert make_bucket_call.args[0] == _REPLICA_BUCKET  # deriva de TENANT_SLUG (header)
    assert "attacker-tenant" not in make_bucket_call.args[0]


@pytest.mark.asyncio
async def test_replica_sin_plan_de_preservacion_no_replica(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sin plan de preservación vigente (`current_num_copias` -> `None`, NO un
    entero) -> CERO réplicas, aunque el feature flag esté encendido. Ejercita
    la rama `if num_copias is None: num_copias = 1` de
    `aip_service.empaquetar_aip` (distinta de `num_copias=1` explícito, ya
    cubierto arriba)."""
    client, mock_conn, mock_minio = client_with_conn
    doc_file_id = uuid.uuid4()
    aip_id = uuid.uuid4()
    retain_until = datetime(2031, 1, 1, tzinfo=UTC)

    monkeypatch.setattr(config_module.settings, "preservation_replica_enabled", True)
    # 1º fetchval = gate RBAC (concedido); 2º = current_num_copias -> SIN plan
    # (None, no un entero); 3º = prev_hash de audit.append.
    mock_conn.fetchval.side_effect = [True, None, None]
    mock_conn.fetchrow.side_effect = _aip_fetchrow_sequence(
        doc_file_id=doc_file_id, aip_id=aip_id, retain_until=retain_until
    )
    mock_minio.get_object = minio_get_object(_DOC_CONTENT)

    resp = await client.post(
        "/api/v1/preservacion/aip",
        json=_base_payload(doc_file_id=doc_file_id),
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["replicado"] is False
    assert body["replica_target"] is None
    assert body["replica_copias_hechas"] == 1

    mock_minio.copy_object.assert_not_called()
    mock_minio.put_object.assert_awaited_once()  # solo la subida primaria

    evento_tipos = [
        c.args[2] for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_evento" in c.args[0]
    ]
    assert "REPLICA" not in evento_tipos


@pytest.mark.asyncio
async def test_replica_cross_site_success_usa_put_object_en_endpoint_secundario(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Con `MINIO_SECONDARY_ENDPOINT` configurado -> réplica CROSS-SITE: un
    SEGUNDO cliente MinIO (host distinto) recibe `put_object` (nunca
    `copy_object` -- no hay copia server-side entre endpoints distintos), y
    el evento REPLICA queda etiquetado `target=cross-site` (nunca "local"
    cuando hay un endpoint secundario real)."""
    client, mock_conn, mock_minio = client_with_conn
    doc_file_id = uuid.uuid4()
    aip_id = uuid.uuid4()
    retain_until = datetime(2031, 1, 1, tzinfo=UTC)

    monkeypatch.setattr(config_module.settings, "preservation_replica_enabled", True)
    monkeypatch.setattr(
        config_module.settings, "minio_secondary_endpoint", "secondary.minio.local:9000"
    )
    monkeypatch.setattr(config_module.settings, "minio_secondary_access_key", "sec-key")
    monkeypatch.setattr(config_module.settings, "minio_secondary_secret_key", "sec-secret")

    secondary_minio = _make_mock_secondary_minio()
    secondary_minio.get_object = _minio_get_con_replica(mock_minio)
    # Singleton perezoso del módulo: se fuerza a None y se sustituye el
    # constructor para que la próxima llamada devuelva NUESTRO mock (nunca un
    # cliente real hacia "secondary.minio.local").
    monkeypatch.setattr(aip_replica_module, "_secondary_minio", None)
    monkeypatch.setattr(
        aip_replica_module, "_get_secondary_minio", lambda: secondary_minio
    )

    _grant_permission(mock_conn, num_copias=2)
    mock_conn.fetchrow.side_effect = _aip_fetchrow_sequence(
        doc_file_id=doc_file_id, aip_id=aip_id, retain_until=retain_until
    ) + [_replica_evento_row(resultado="ok")]
    mock_minio.get_object = _minio_get_con_replica(mock_minio)

    resp = await client.post(
        "/api/v1/preservacion/aip",
        json=_base_payload(doc_file_id=doc_file_id),
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["replicado"] is True
    assert body["replica_target"] == "cross-site"
    assert body["replica_copias_hechas"] == 2

    # --- bucket réplica provisionado EN EL SEGUNDO cliente, con Object Lock ---
    make_bucket_call = secondary_minio.make_bucket.await_args
    assert make_bucket_call.args[0] == _REPLICA_BUCKET
    assert make_bucket_call.kwargs["object_lock"] is True

    # --- put_object (no copy_object) en el cliente SECUNDARIO, mismo object_key ---
    primary_put_call = mock_minio.put_object.await_args
    primary_object_key = primary_put_call.args[1]

    secondary_minio.put_object.assert_awaited_once()
    secondary_put_call = secondary_minio.put_object.await_args
    assert secondary_put_call.args[0] == _REPLICA_BUCKET
    assert secondary_put_call.args[1] == primary_object_key
    assert secondary_put_call.kwargs["retention"].mode == "COMPLIANCE"
    assert secondary_put_call.kwargs["retention"].retain_until_date == retain_until
    assert secondary_put_call.kwargs["legal_hold"] is False

    # --- el cliente PRIMARIO nunca hace copy_object en el camino cross-site ---
    mock_minio.copy_object.assert_not_called()

    # --- evento REPLICA ok, etiquetado HONESTAMENTE como cross-site ---
    replica_evt = [
        c for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_evento" in c.args[0] and c.args[2] == "REPLICA"
    ]
    assert len(replica_evt) == 1
    assert replica_evt[0].args[3] == "ok"
    import json as _json

    detalle = _json.loads(replica_evt[0].args[6])
    assert detalle["target"] == "cross-site"
    assert "cross-site" in detalle["nota"]
    assert "NO es copia geográfica" not in detalle["nota"]


# ---------------------------------------------------------------------------
# AREA-08-04 — la réplica se RELEE (retención + fixity), el secundario se valida y
# el motivo de fallo no filtra el texto del almacén.
# ---------------------------------------------------------------------------


async def _empaquetar_con_replica(client_with_conn, monkeypatch, *, estado_replica: str):
    client, mock_conn, mock_minio = client_with_conn
    doc_file_id = uuid.uuid4()
    retain_until = datetime(2031, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(config_module.settings, "preservation_replica_enabled", True)
    _grant_permission(mock_conn, num_copias=2)
    mock_conn.fetchrow.side_effect = _aip_fetchrow_sequence(
        doc_file_id=doc_file_id, aip_id=uuid.uuid4(), retain_until=retain_until
    ) + [_replica_evento_row(resultado=estado_replica)]
    mock_minio.bucket_exists = AsyncMock(side_effect=_only_replica_bucket_missing)
    return client, mock_conn, mock_minio, doc_file_id


@pytest.mark.asyncio
async def test_replica_con_bytes_distintos_no_es_ok(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La copia releída no tiene el SHA-256 del ZIP -> `ok=False`, motivo
    `fixity_replica`. Antes el evento REPLICA decía `ok` sobre una copia que nadie
    había mirado."""
    client, mock_conn, mock_minio, doc_file_id = await _empaquetar_con_replica(
        client_with_conn, monkeypatch, estado_replica="fallo"
    )
    mock_minio.get_object = _minio_get_con_replica(
        mock_minio, bytes_de_la_replica=b"bytes corruptos de la copia"
    )

    resp = await client.post(
        "/api/v1/preservacion/aip",
        json=_base_payload(doc_file_id=doc_file_id),
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["replicado"] is False
    replica_evt = [
        c for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_evento" in c.args[0] and c.args[2] == "REPLICA"
    ][0]
    assert replica_evt.args[3] == "fallo"
    import json as _json

    assert _json.loads(replica_evt.args[6])["motivo_fallo"] == "fixity_replica"


@pytest.mark.asyncio
async def test_replica_con_retencion_distinta_no_es_ok(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El almacén acepta la copia pero la retención releída es más corta -> no ok."""
    from miniopy_async.retention import Retention

    client, mock_conn, mock_minio, doc_file_id = await _empaquetar_con_replica(
        client_with_conn, monkeypatch, estado_replica="fallo"
    )
    mock_minio.get_object = _minio_get_con_replica(mock_minio)
    primera = {"hecha": False}

    async def _retencion(bucket, *_a, **_k):
        # primario: lo pedido; réplica: una retención corta
        if bucket == _REPLICA_BUCKET:
            return Retention("COMPLIANCE", datetime(2026, 1, 1, tzinfo=UTC))
        primera["hecha"] = True
        return Retention("COMPLIANCE", datetime(2031, 1, 1, tzinfo=UTC))

    mock_minio.get_object_retention = AsyncMock(side_effect=_retencion)

    resp = await client.post(
        "/api/v1/preservacion/aip",
        json=_base_payload(doc_file_id=doc_file_id),
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["replicado"] is False
    import json as _json

    replica_evt = [
        c for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_evento" in c.args[0] and c.args[2] == "REPLICA"
    ][0]
    assert _json.loads(replica_evt.args[6])["motivo_fallo"] == "RetencionNoVerificadaError"


@pytest.mark.asyncio
async def test_secundario_igual_al_primario_se_etiqueta_local(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`MINIO_SECONDARY_ENDPOINT` == `MINIO_ENDPOINT` (aunque con esquema o mayúsculas)
    NO es cross-site: target `local`, copia server-side en el primario."""
    client, mock_conn, mock_minio, doc_file_id = await _empaquetar_con_replica(
        client_with_conn, monkeypatch, estado_replica="ok"
    )
    mock_minio.get_object = _minio_get_con_replica(mock_minio)
    monkeypatch.setattr(config_module.settings, "minio_endpoint", "minio:9000")
    monkeypatch.setattr(
        config_module.settings, "minio_secondary_endpoint", "http://MINIO:9000/"
    )
    monkeypatch.setattr(
        aip_replica_module,
        "_get_secondary_minio",
        lambda: pytest.fail("no debe usarse un cliente secundario para el mismo host"),
    )

    resp = await client.post(
        "/api/v1/preservacion/aip",
        json=_base_payload(doc_file_id=doc_file_id),
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["replica_target"] == "local"
    mock_minio.copy_object.assert_awaited_once()


@pytest.mark.asyncio
async def test_motivo_de_fallo_s3_no_filtra_host_ni_request_id(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un `S3Error` cuyo texto lleva host y request-id llega al evento solo como
    `S3Error:<code>`."""
    from miniopy_async import S3Error

    client, mock_conn, mock_minio, doc_file_id = await _empaquetar_con_replica(
        client_with_conn, monkeypatch, estado_replica="fallo"
    )
    mock_minio.get_object = _minio_get_con_replica(mock_minio)
    mock_minio.copy_object = AsyncMock(
        side_effect=S3Error(
            "AccessDenied", "denegado en minio:9000 bucket x", "/b/k", "REQID-123", "HOSTID-9", None
        )
    )

    resp = await client.post(
        "/api/v1/preservacion/aip",
        json=_base_payload(doc_file_id=doc_file_id),
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    import json as _json

    replica_evt = [
        c for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_evento" in c.args[0] and c.args[2] == "REPLICA"
    ][0]
    motivo = _json.loads(replica_evt.args[6])["motivo_fallo"]
    assert motivo == "S3Error:AccessDenied"
    assert "minio:9000" not in replica_evt.args[6] and "REQID" not in replica_evt.args[6]
