# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests unitarios directos del worker asíncrono de validación PDF/A en
ingesta (`app.services.pdfa_ingest_service.run_ingest_validation_background`,
E10 Increment C2b follow-up) -- SIN pasar por HTTP, con una conexión/pool
propios (mockeados). Complementa `tests/test_pdfa_ingest_upload.py` (que
cubre el router/StorageService) y
`tests/integration/test_pdfa_ingest_worker_real_sql.py` (la MISMA garantía
de aislamiento multi-tenant contra Postgres real).

`_FakePool` imita `asyncpg.Pool.acquire()` como context manager async que
siempre devuelve la MISMA conexión mockeada -- suficiente para este carril
unitario, donde lo que se prueba es la SECUENCIA de llamadas sobre esa
conexión, no la concurrencia real del pool (eso es trabajo del tier de
integración).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.pdfa_ingest_service import (
    WORKER_AGENT,
    run_ingest_validation_background,
)
from tests.conftest import TENANT_SLUG, make_file_record, minio_get_object

_PDF_CONTENT = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\ncontenido pdf real (worker)\n%%EOF"
_NON_PDF_CONTENT = b"esto no es un PDF"


class _FakeAcquireCtx:
    def __init__(self, conn) -> None:
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakePool:
    """Doble mínimo de `asyncpg.Pool`: `.acquire()` siempre devuelve la
    MISMA conexión mockeada -- el worker adquiere su PROPIA conexión de
    este pool en cada llamada, tal como en producción hace con el pool real
    del `app.state`."""

    def __init__(self, conn) -> None:
        self._conn = conn

    def acquire(self) -> _FakeAcquireCtx:
        return _FakeAcquireCtx(self._conn)


def _evento_row(file_id: uuid.UUID, *, resultado: str = "no_evaluado") -> dict:
    return {
        "id": uuid.uuid4(),
        "object_ref": str(file_id),
        "tipo": "VALIDACION_PDFA",
        "resultado": resultado,
        "hash_before": None,
        "hash_after": None,
        "detalle": None,
        "agente": WORKER_AGENT,
        "ts": None,
    }


def _update_calls(conn: AsyncMock) -> list:
    return [
        c
        for c in conn.execute.call_args_list
        if c.args and "UPDATE files SET pdfa_status = $2" in c.args[0]
    ]


def _search_path_calls(conn: AsyncMock) -> list:
    return [c for c in conn.execute.call_args_list if c.args and "SET search_path" in c.args[0]]


# ---------------------------------------------------------------------------
# Camino normal: fija el veredicto TERMINAL + evento PREMIS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_happy_path_writes_terminal_status_and_premis_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file_id = uuid.uuid4()
    record = make_file_record(
        file_id=file_id,
        mime_type="application/pdf",
        object_key=f"{file_id}/oficio.pdf",
    )
    conn = AsyncMock()
    conn.fetchrow.side_effect = [record, _evento_row(file_id)]

    minio = MagicMock()
    minio.get_object = minio_get_object(_PDF_CONTENT)
    monkeypatch.setattr("app.services.pdfa_ingest_service.get_minio", lambda: minio)

    await run_ingest_validation_background(_FakePool(conn), TENANT_SLUG, file_id)

    update_calls = _update_calls(conn)
    assert len(update_calls) == 1
    assert update_calls[0].args[1] == file_id
    assert update_calls[0].args[2] == "not_evaluated"  # stub honesto (PDFA_VALIDATOR=stub, default)

    evento_calls = [
        c
        for c in conn.fetchrow.call_args_list
        if c.args and "INSERT INTO preservacion_evento" in c.args[0]
    ]
    assert len(evento_calls) == 1
    assert evento_calls[0].args[1] == str(file_id)  # object_ref = file_id
    assert evento_calls[0].args[2] == "VALIDACION_PDFA"
    assert evento_calls[0].args[7] == WORKER_AGENT  # agente = worker, no un usuario real

    # search_path del schema del tenant correcto en cada llamada + reset final a public.
    tenant_calls = [c for c in _search_path_calls(conn) if f'"tenant_{TENANT_SLUG}"' in c.args[0]]
    assert len(tenant_calls) >= 1
    assert conn.execute.call_args_list[-1].args == ("SET search_path = public",)


@pytest.mark.asyncio
async def test_worker_file_not_found_noop_no_download_no_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El objeto pudo purgarse (disposición TRD) entre la subida y la
    corrida del worker -- nada que descargar ni persistir, sin excepción."""
    conn = AsyncMock()
    conn.fetchrow.side_effect = [None]

    minio = MagicMock()
    minio.get_object = AsyncMock()
    monkeypatch.setattr("app.services.pdfa_ingest_service.get_minio", lambda: minio)

    await run_ingest_validation_background(_FakePool(conn), TENANT_SLUG, uuid.uuid4())

    minio.get_object.assert_not_called()
    assert _update_calls(conn) == []


@pytest.mark.asyncio
async def test_worker_defensive_recheck_skips_object_replaced_by_non_pdf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defensa en profundidad: el objeto ya no es PDF cuando el worker
    corre (p. ej. `StorageService.replace` lo reemplazó en la ventana entre
    encolar y ejecutar) -- se descarta sin validar ni escribir nada."""
    file_id = uuid.uuid4()
    record = make_file_record(file_id=file_id, object_key=f"{file_id}/oficio.pdf")
    conn = AsyncMock()
    conn.fetchrow.side_effect = [record]

    minio = MagicMock()
    minio.get_object = minio_get_object(_NON_PDF_CONTENT)
    monkeypatch.setattr("app.services.pdfa_ingest_service.get_minio", lambda: minio)

    await run_ingest_validation_background(_FakePool(conn), TENANT_SLUG, file_id)

    assert _update_calls(conn) == []


# ---------------------------------------------------------------------------
# NUNCA lanza -- best-effort post-respuesta
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_never_raises_even_if_minio_explodes(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    file_id = uuid.uuid4()
    record = make_file_record(file_id=file_id, object_key=f"{file_id}/oficio.pdf")
    conn = AsyncMock()
    conn.fetchrow.side_effect = [record]

    def _boom() -> None:
        raise RuntimeError("MinIO caído (forzado por el test)")

    monkeypatch.setattr("app.services.pdfa_ingest_service.get_minio", _boom)

    with caplog.at_level("ERROR"):
        # No debe lanzar -- si lo hiciera, este `await` propagaría y el test fallaría.
        await run_ingest_validation_background(_FakePool(conn), TENANT_SLUG, file_id)

    assert any("Validación PDF/A de ingesta" in r.getMessage() for r in caplog.records)
    # El `finally` interno (reset a public) sigue corriendo aunque la
    # excepción se levante DENTRO del `async with pool.acquire()`.
    assert conn.execute.call_args_list[-1].args == ("SET search_path = public",)


@pytest.mark.asyncio
async def test_worker_run_twice_same_file_id_converges_to_coherent_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Correr el worker dos veces para el MISMO `file_id` (p. ej. un
    reintento manual) no debe romper nada -- `update_pdfa_result` es un
    UPDATE simple (trivialmente idempotente en el valor final: ambas
    corridas fijan el MISMO veredicto, aunque `preservacion_evento` sea
    append-only y por tanto acumule un evento por corrida, igual que el
    resto del rastro PREMIS de este servicio)."""
    file_id = uuid.uuid4()
    record = make_file_record(file_id=file_id, object_key=f"{file_id}/oficio.pdf")
    conn = AsyncMock()
    conn.fetchrow.side_effect = [record, _evento_row(file_id), record, _evento_row(file_id)]

    minio = MagicMock()
    minio.get_object = minio_get_object(_PDF_CONTENT)
    monkeypatch.setattr("app.services.pdfa_ingest_service.get_minio", lambda: minio)

    pool = _FakePool(conn)
    await run_ingest_validation_background(pool, TENANT_SLUG, file_id)
    await run_ingest_validation_background(pool, TENANT_SLUG, file_id)

    update_calls = _update_calls(conn)
    assert len(update_calls) == 2
    assert {c.args[1] for c in update_calls} == {file_id}
    assert {c.args[2] for c in update_calls} == {"not_evaluated"}  # mismo veredicto ambas veces


# ---------------------------------------------------------------------------
# Remediación de auditoría Media (E07): slug de tenant con guion (válido y
# habitual en TODO el resto del sistema) -- el worker DEBE aterrizar el
# veredicto igual que con cualquier otro slug.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hyphenated_tenant_slug_worker_lands_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Antes de la remediación, `FileRepository._SLUG_RE` (`^[a-z0-9_]+$`)
    rechazaba CUALQUIER guion -- a diferencia de
    `PreservacionRepository`/`PreservacionWormRepository`/
    `app.core.database.tenant_schema()`, que ya normalizaban '-'->'_'. Un
    `tenant_slug` con guion -- la forma VÁLIDA y habitual de un slug real
    (tenant-service los admite, ver
    `services/tenant-service/app/schemas/tenant.py`) -- hacía que
    `FileRepository.__init__` levantara `InvalidTenantSlugError` ANTES de
    cualquier I/O, y el worker se retiraba en silencio, para siempre.

    `FileRepository` ahora reusa la MISMA regex/normalización canónica que
    `tenant_schema()` (`app.core.database.TENANT_SLUG_RE`) -- este test
    demuestra que un slug guionado sigue EXACTAMENTE el mismo camino feliz
    que `test_worker_happy_path_writes_terminal_status_and_premis_event`."""
    slug = "acme-corp"
    file_id = uuid.uuid4()
    record = make_file_record(
        file_id=file_id,
        mime_type="application/pdf",
        object_key=f"{file_id}/oficio.pdf",
    )
    conn = AsyncMock()
    conn.fetchrow.side_effect = [record, _evento_row(file_id)]

    minio = MagicMock()
    minio.get_object = minio_get_object(_PDF_CONTENT)
    monkeypatch.setattr("app.services.pdfa_ingest_service.get_minio", lambda: minio)

    await run_ingest_validation_background(_FakePool(conn), slug, file_id)

    update_calls = _update_calls(conn)
    assert len(update_calls) == 1
    assert update_calls[0].args[1] == file_id
    assert update_calls[0].args[2] == "not_evaluated"  # stub honesto (PDFA_VALIDATOR=stub, default)

    # El schema derivado normaliza el guion a guion bajo, igual que `tenant_schema()`.
    tenant_calls = [c for c in _search_path_calls(conn) if '"tenant_acme_corp"' in c.args[0]]
    assert len(tenant_calls) >= 1
    assert conn.execute.call_args_list[-1].args == ("SET search_path = public",)
