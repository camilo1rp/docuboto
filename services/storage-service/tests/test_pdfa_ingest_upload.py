# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests de validación PDF/A en la INGESTA de anexos (E07, E10 Increment C2b
follow-up) -- ``POST /api/v1/storage/upload``, capa router + StorageService.

DB y MinIO mockeados (`tests/conftest.py`). Cubre:
  - Gate maestro OFF (default): retrocompat total, ni una columna pdfa_* se
    toca, no se agenda BackgroundTask (aunque el modo por defecto sea
    "advisory" y el contenido SEA un PDF real).
  - Modo advisory (default con el gate ON): la subida NUNCA se bloquea --
    marca `pdfa_status='pendiente'` SÍNCRONO y agenda
    `run_ingest_validation_background` como `BackgroundTasks`, con
    `(app.state.db_pool, tenant_slug, file_id)` exactos. Un documento
    detectado como NO-PDF (magic bytes) no agenda nada.
  - Modo strict (opt-in): `non_conformant` rechaza con 422 ANTES de tocar
    MinIO/BD (nada persiste); `conformant`/`not_evaluated` proceden normal
    y el veredicto queda escrito SÍNCRONAMENTE (sin BackgroundTask).

`app.state.db_pool` solo se referencia en el router cuando SÍ se agenda un
BackgroundTask (`if schedule_pdfa_validation:`) -- los tests que no llegan
ahí (gate OFF, no-PDF) no necesitan configurarlo.
"""

from __future__ import annotations

import hashlib
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

import app.core.config as config_module
import app.routers.storage as storage_router_module
from app.main import app as fastapi_app
from app.services.pdfa import PdfaResult
from tests.conftest import (
    TENANT_HEADERS,
    TENANT_SLUG,
    conceder_permisos,
    make_file_record,
)

_PDF_CONTENT = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\ncontenido pdf real (ingesta)\n%%EOF"
_PDF_SHA256 = hashlib.sha256(_PDF_CONTENT).hexdigest()

_NON_PDF_CONTENT = b"este anexo es un .txt corriente, no un PDF"
_NON_PDF_SHA256 = hashlib.sha256(_NON_PDF_CONTENT).hexdigest()

_FILENAME = "oficio.pdf"
_MIME_TYPE = "application/pdf"

_DB_POOL_SENTINEL = object()


def _new_record(file_id: uuid.UUID, *, sha256: str = _PDF_SHA256) -> dict:
    return make_file_record(
        file_id=file_id,
        filename=_FILENAME,
        mime_type=_MIME_TYPE,
        sha256=sha256,
        object_key=f"{file_id}/{_FILENAME}",
    )


def _pdfa_pending_execute_calls(mock_conn: AsyncMock) -> list:
    return [
        c
        for c in mock_conn.execute.call_args_list
        if c.args and "pdfa_status = 'pendiente'" in c.args[0]
    ]


def _pin_upload_file_id(monkeypatch: pytest.MonkeyPatch, file_id: uuid.UUID) -> None:
    """`StorageService.upload` genera su PROPIO `file_id` internamente
    (``uuid.uuid4()``, nunca lo toma de la fila mockeada que devuelve
    ``_repo.create``) -- ese es el valor real que viaja a
    `mark_pdfa_pending`/`record_ingest_pdfa_result`/el `BackgroundTask`
    agendado. Para poder afirmar sobre CUÁL `file_id` recibieron esas
    llamadas, se fija el generador a un valor conocido."""
    monkeypatch.setattr("app.services.storage_service.uuid.uuid4", lambda: file_id)


# ---------------------------------------------------------------------------
# Gate maestro OFF (default) -- retrocompat total
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_off_by_default_upload_behaves_exactly_as_before(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`PRESERVATION_PDFA_VALIDATION_ENABLED=false` (default de fábrica):
    subir un PDF real no toca ninguna columna pdfa_*, no agenda el
    BackgroundTask -- IDÉNTICO al comportamiento previo a este follow-up,
    aunque `PRESERVATION_PDFA_INGEST_MODE` siga en su default "advisory"."""
    assert config_module.settings.preservation_pdfa_validation_enabled is False
    assert config_module.settings.preservation_pdfa_ingest_mode == "advisory"
    client, mock_conn, mock_minio = client_with_conn
    conceder_permisos(mock_conn)  # `/upload` exige PERM_RADI

    file_id = uuid.uuid4()
    record = _new_record(file_id)
    mock_conn.fetchrow.side_effect = [None, record]

    spy = AsyncMock()
    monkeypatch.setattr(storage_router_module, "run_ingest_validation_background", spy)
    # A propósito NO se configura `app.state.db_pool`: si el router lo
    # tocara aquí (bug de regresión), el request fallaría con AttributeError
    # -- la propia ausencia del atributo es parte de la prueba.

    resp = await client.post(
        "/api/v1/storage/upload",
        files={"file": (_FILENAME, _PDF_CONTENT, _MIME_TYPE)},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["id"] == str(file_id)
    spy.assert_not_called()
    assert _pdfa_pending_execute_calls(mock_conn) == []


# ---------------------------------------------------------------------------
# Modo advisory (default con el gate ON)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_advisory_pdf_marks_pending_and_schedules_background_task(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate ON + modo advisory (default) + contenido PDF real: la subida
    responde 201 SIN bloquear, marca `pdfa_status='pendiente'` SÍNCRONO y
    agenda `run_ingest_validation_background(app.state.db_pool, tenant_slug,
    file_id)` -- exactamente esos tres argumentos, en ese orden."""
    monkeypatch.setattr(config_module.settings, "preservation_pdfa_validation_enabled", True)
    client, mock_conn, mock_minio = client_with_conn
    conceder_permisos(mock_conn)  # `/upload` exige PERM_RADI

    file_id = uuid.uuid4()
    record = _new_record(file_id)
    mock_conn.fetchrow.side_effect = [None, record]
    _pin_upload_file_id(monkeypatch, file_id)

    spy = AsyncMock()
    monkeypatch.setattr(storage_router_module, "run_ingest_validation_background", spy)
    monkeypatch.setattr(fastapi_app.state, "db_pool", _DB_POOL_SENTINEL, raising=False)

    resp = await client.post(
        "/api/v1/storage/upload",
        files={"file": (_FILENAME, _PDF_CONTENT, _MIME_TYPE)},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201, resp.text

    pending_calls = _pdfa_pending_execute_calls(mock_conn)
    assert len(pending_calls) == 1
    assert pending_calls[0].args[1] == file_id
    assert pending_calls[0].args[2] == config_module.settings.pdfa_profile

    # `BackgroundTasks` corre DENTRO del mismo ciclo ASGI que
    # `AsyncClient(transport=ASGITransport(...))` ya esperó -- para cuando
    # `await client.post(...)` retorna, la tarea agendada ya fue invocada.
    spy.assert_awaited_once_with(_DB_POOL_SENTINEL, TENANT_SLUG, file_id)


@pytest.mark.asyncio
async def test_advisory_non_pdf_never_schedules_background_task(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate ON + advisory, pero el contenido NO es PDF (magic bytes): no se
    marca `pendiente` ni se agenda nada -- el follow-up solo aplica a PDFs."""
    monkeypatch.setattr(config_module.settings, "preservation_pdfa_validation_enabled", True)
    client, mock_conn, mock_minio = client_with_conn
    conceder_permisos(mock_conn)  # `/upload` exige PERM_RADI

    file_id = uuid.uuid4()
    record = make_file_record(
        file_id=file_id,
        filename="nota.txt",
        mime_type="text/plain",
        sha256=_NON_PDF_SHA256,
        object_key=f"{file_id}/nota.txt",
    )
    mock_conn.fetchrow.side_effect = [None, record]

    spy = AsyncMock()
    monkeypatch.setattr(storage_router_module, "run_ingest_validation_background", spy)
    # Sin `app.state.db_pool`: si el router intentara agendar igual, este
    # test fallaría con AttributeError en vez de un falso verde.

    resp = await client.post(
        "/api/v1/storage/upload",
        files={"file": ("nota.txt", _NON_PDF_CONTENT, "text/plain")},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    spy.assert_not_called()
    assert _pdfa_pending_execute_calls(mock_conn) == []


# ---------------------------------------------------------------------------
# Modo strict (opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_strict_non_conformant_rejects_before_touching_minio_or_db(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Modo strict + `non_conformant`: 422 `pdfa_non_conformant`, y NADA
    persiste -- ni objeto en MinIO (`put_object` nunca se llama) ni fila en
    `files` (el único `fetchrow` ejecutado es la dedup por sha256, que
    devuelve None). El rechazo SÍ se audita, keyed por sha256 (no hay
    `file_id`: el archivo nunca se creó)."""
    monkeypatch.setattr(config_module.settings, "preservation_pdfa_validation_enabled", True)
    monkeypatch.setattr(config_module.settings, "preservation_pdfa_ingest_mode", "strict")
    client, mock_conn, mock_minio = client_with_conn
    conceder_permisos(mock_conn)  # `/upload` exige PERM_RADI

    monkeypatch.setattr(
        "app.services.storage_service.validate_ingest_content",
        AsyncMock(
            return_value=PdfaResult(
                outcome="non_conformant",
                profile="2b",
                validator_agent="test/forced-non-conformant",
                detail={"motivo": "forzado por el test"},
            )
        ),
    )

    resp = await client.post(
        "/api/v1/storage/upload",
        files={"file": (_FILENAME, _PDF_CONTENT, _MIME_TYPE)},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error"] == "pdfa_non_conformant"

    mock_minio.put_object.assert_not_awaited()
    # find_by_sha256 fue el ÚNICO fetchrow -- nunca se llegó al INSERT de `files`.
    assert mock_conn.fetchrow.call_count == 1

    audit_inserts = [
        c
        for c in mock_conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_inserts) == 1
    # params de audit._INSERT_SQL: (sql, tenant, service, actor, canal, action, object_type, object_ref, ...)
    audit_call = audit_inserts[0]
    assert audit_call.args[5] == "file.pdfa_rejected_ingest"
    assert audit_call.args[6] == "file"
    assert audit_call.args[7] == _PDF_SHA256


def test_looks_like_pdf_tolera_basura_antes_de_la_cabecera() -> None:
    """AREA-08-02: los lectores aceptan hasta 1 KiB delante de `%PDF-`; con
    `startswith`, un PDF con prefijo saltaba la validación en modo estricto."""
    from app.services.pdfa_ingest_service import looks_like_pdf

    assert looks_like_pdf(b"\x00\x00\r\n%PDF-1.7\ncuerpo")
    assert looks_like_pdf(_PDF_CONTENT)
    assert not looks_like_pdf(b"no es un pdf")
    # fuera de la ventana de 1 KiB ya no cuenta
    assert not looks_like_pdf(b"x" * 2048 + b"%PDF-1.7")


@pytest.mark.asyncio
async def test_strict_pdf_con_prefijo_se_valida_y_se_rechaza(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Modo strict: un PDF con bytes basura delante, `non_conformant`, da 422 y no
    persiste (antes saltaba el validador por el sniff en offset 0)."""
    monkeypatch.setattr(config_module.settings, "preservation_pdfa_validation_enabled", True)
    monkeypatch.setattr(config_module.settings, "preservation_pdfa_ingest_mode", "strict")
    client, mock_conn, mock_minio = client_with_conn
    conceder_permisos(mock_conn)
    monkeypatch.setattr(
        "app.services.storage_service.validate_ingest_content",
        AsyncMock(
            return_value=PdfaResult(
                outcome="non_conformant", profile="2b", validator_agent="t", detail={}
            )
        ),
    )

    resp = await client.post(
        "/api/v1/storage/upload",
        files={"file": (_FILENAME, b"\x00\x00JUNK" + _PDF_CONTENT, _MIME_TYPE)},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 422, resp.text
    mock_minio.put_object.assert_not_awaited()


@pytest.mark.parametrize("outcome", ["conformant", "not_evaluated"])
@pytest.mark.asyncio
async def test_strict_conformant_or_not_evaluated_proceeds_and_persists_verdict(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    """Modo strict, resultados que NO son `non_conformant` (incluido el stub
    honesto por defecto -> `not_evaluated`): la subida procede normal (201) y
    el veredicto queda escrito SÍNCRONAMENTE (`files.pdfa_status` +
    `preservacion_evento`) -- sin BackgroundTask, porque ya se resolvió
    antes de responder."""
    monkeypatch.setattr(config_module.settings, "preservation_pdfa_validation_enabled", True)
    monkeypatch.setattr(config_module.settings, "preservation_pdfa_ingest_mode", "strict")
    client, mock_conn, mock_minio = client_with_conn
    conceder_permisos(mock_conn)  # `/upload` exige PERM_RADI

    monkeypatch.setattr(
        "app.services.storage_service.validate_ingest_content",
        AsyncMock(
            return_value=PdfaResult(
                outcome=outcome,  # type: ignore[arg-type]
                profile="2b",
                validator_agent="test/forced",
                detail={},
            )
        ),
    )

    spy = AsyncMock()
    monkeypatch.setattr(storage_router_module, "run_ingest_validation_background", spy)

    file_id = uuid.uuid4()
    record = _new_record(file_id)
    _pin_upload_file_id(monkeypatch, file_id)
    evento_row = {
        "id": uuid.uuid4(),
        "object_ref": str(file_id),
        "tipo": "VALIDACION_PDFA",
        "resultado": "ok" if outcome == "conformant" else "no_evaluado",
        "hash_before": None,
        "hash_after": None,
        "detalle": None,
        "agente": "ingesta-http",
        "ts": record["uploaded_at"],
    }
    mock_conn.fetchrow.side_effect = [None, record, evento_row]

    resp = await client.post(
        "/api/v1/storage/upload",
        files={"file": (_FILENAME, _PDF_CONTENT, _MIME_TYPE)},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    mock_minio.put_object.assert_awaited_once()
    spy.assert_not_called()  # strict es síncrono, nunca agenda el worker

    update_calls = [
        c
        for c in mock_conn.execute.call_args_list
        if c.args and "UPDATE files SET pdfa_status = $2" in c.args[0]
    ]
    assert len(update_calls) == 1
    assert update_calls[0].args[1] == file_id
    assert update_calls[0].args[2] == outcome

    evento_calls = [
        c
        for c in mock_conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_evento" in c.args[0]
    ]
    assert len(evento_calls) == 1
    assert evento_calls[0].args[2] == "VALIDACION_PDFA"
