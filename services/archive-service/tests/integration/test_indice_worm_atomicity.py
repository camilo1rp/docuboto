# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Cableado WORM/Object-Lock del índice electrónico (E10 Inc.1) contra
Postgres REAL: atomicidad marca<->asiento de `IndexService.proteger_worm_indice`
(commit conjunto, rollback conjunto ante un fallo tras `audit.append`),
idempotencia bajo dos llamadas reales (secuenciales y concurrentes) sobre el
MISMO índice, derivación de retención fail-closed contra datos TRD reales sin
años declarados, y el filtro SQL de `IndexRepository.list_worm_pending`
(backoff/agotamiento).

Compromiso (igual que el resto de `tests/integration/`): NO hay tráfico HTTP
real a storage-service — `storage_client.proteger_indice_worm_internal` se
monkeypatchea a un `WormProtectionOutcome` fijo. Lo que este tier prueba es
el tramo 100%-DB (UPDATE + INSERT audit_log atómicos, WHERE de idempotencia,
derivación TRD), no la subida real a MinIO (cubierta en storage-service).

Seed: el índice se marca 'firmado' con un UPDATE directo (mismo atajo que
`test_atomicity.py`/`test_indice_reconciliation_atomicity.py` usan para no
depender de signature-service real) — `ArchiveService.close_expediente(...,
actor_id=None)` deja el índice 'pendiente_firma' (motivo='sin_actor_id'),
y este seed lo promueve a 'firmado' directamente.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from orpycamcp_common import audit

from app.clients.storage_client import WormProtectionOutcome
from app.core.database import tenant_schema
from app.repositories.index_repository import IndexRepository
from app.schemas.archive import ExpedienteCreate, TrdSerieCreate
from app.services.archive_service import ArchiveService
from app.services.index_service import IndexService

from .conftest import DSN


async def _seed_expediente_firmado(
    conn: asyncpg.Connection,
    slug: str,
    name: str,
    *,
    archivo_gestion_years: int | None = 5,
    archivo_central_years: int | None = 5,
) -> tuple[uuid.UUID, asyncpg.Record]:
    """Expediente 'closed' con una serie TRD real (retención declarada por
    parámetro) y su índice vigente promovido a 'firmado' directamente (sin
    signature-service real, ver docstring del módulo). Devuelve
    ``(expediente_id, indice_row)``."""
    schema = tenant_schema(slug)
    archive = ArchiveService(conn, slug)
    serie = await archive.create_trd_serie(
        TrdSerieCreate(
            code=f"WRM{uuid.uuid4().hex[:6]}",
            name="itest serie worm",
            # `retention_years`/`total_retention` en 0: `ArchiveRepository.
            # create_trd_serie` usa `retention_years`/`max(total_retention -
            # retention_years, 0)` como FALLBACK cuando
            # `archivo_gestion_years`/`archivo_central_years` llegan en
            # `None` — para el escenario "TRD sin retención declarada" (total
            # derivado real = 0) hace falta anular también el fallback, no
            # solo pasar `None` en las dos fases.
            retention_years=0,
            total_retention=0,
            archivo_gestion_years=archivo_gestion_years,
            archivo_central_years=archivo_central_years,
        ),
        actor=None,
    )
    created = await archive.create_expediente(
        payload=ExpedienteCreate(name=name, nivel_seguridad=1, trd_serie_id=serie.id),
        opened_by=None,
    )
    await archive.close_expediente(created.id)  # actor_id=None -> índice 'pendiente_firma'

    file_id = uuid.uuid4()
    row = await conn.fetchrow(
        f"""
        UPDATE "{schema}".expediente_indice
        SET estado = 'firmado', firma_id = $2, minio_key_firmado = $3
        WHERE expediente_id = $1 AND estado <> 'firmado'
        RETURNING id, expediente_id, minio_key_firmado, worm_reconcile_intentos
        """,
        created.id,
        uuid.uuid4(),
        f"{file_id}/indice-{created.id}.xml",
    )
    assert row is not None
    return created.id, row


def _ok_outcome() -> WormProtectionOutcome:
    return WormProtectionOutcome(ok=True, worm_object_id=uuid.uuid4())


# ---------------------------------------------------------------------------
# (a) Atomicidad marca<->asiento: commit conjunto en el camino feliz.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_proteger_worm_indice_commits_mark_and_audit_together(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    expediente_id, row = await _seed_expediente_firmado(conn, slug, "itest worm OK")
    outcome = _ok_outcome()

    with patch(
        "app.clients.storage_client.proteger_indice_worm_internal",
        new=AsyncMock(return_value=outcome),
    ):
        resultado = await IndexService(conn, slug).proteger_worm_indice(
            expediente_id, row["id"], actor_id=None, origen="reconciliation"
        )

    assert resultado == "protegido"

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        indice_row = await second_conn.fetchrow(
            f'SELECT worm_protegido_at, worm_object_id, worm_reconcile_intentos '
            f'FROM "{schema}".expediente_indice WHERE id = $1',
            row["id"],
        )
        audit_row = await second_conn.fetchrow(
            "SELECT actor, canal, payload FROM public.audit_log "
            "WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.indice_worm_protegido",
        )
    finally:
        await second_conn.close()

    assert indice_row["worm_protegido_at"] is not None
    assert indice_row["worm_object_id"] == outcome.worm_object_id
    assert audit_row is not None
    assert audit_row["canal"] == "system"

    ok, broken_id = await audit.verify_chain(conn, slug)
    assert ok is True
    assert broken_id is None


# ---------------------------------------------------------------------------
# (b) Atomicidad marca<->asiento: fallo DESPUÉS de audit.append revierte
# ambos, aunque `proteger_worm_indice` se trague la excepción (best-effort
# real: nunca propaga, pero tampoco deja el marcado a medias).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_proteger_worm_indice_failure_after_audit_append_reverts_mark(
    tenant_ctx: tuple[str, asyncpg.Connection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    expediente_id, row = await _seed_expediente_firmado(conn, slug, "itest worm boom")

    original_append = audit.append

    async def _append_then_boom(*args: object, **kwargs: object) -> str:
        row_hash = await original_append(*args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError("boom-post-audit (worm, b2 fault injection)")

    monkeypatch.setattr(audit, "append", _append_then_boom)

    with patch(
        "app.clients.storage_client.proteger_indice_worm_internal",
        new=AsyncMock(return_value=_ok_outcome()),
    ):
        resultado = await IndexService(conn, slug).proteger_worm_indice(
            expediente_id, row["id"], actor_id=None, origen="reconciliation"
        )

    # Best-effort real: NUNCA propaga la excepción al llamador...
    assert resultado == "pendiente"

    # ...pero el UPDATE revirtió JUNTO con el asiento que sí llegó a
    # ejecutarse (dentro del SAVEPOINT de audit.append) antes de que la
    # excepción reventara la tx externa de `proteger_worm_indice`.
    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        indice_row = await second_conn.fetchrow(
            f'SELECT worm_protegido_at, worm_object_id FROM "{schema}".expediente_indice '
            "WHERE id = $1",
            row["id"],
        )
        protegido_audit_count = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.indice_worm_protegido",
        )
    finally:
        await second_conn.close()

    assert indice_row["worm_protegido_at"] is None
    assert indice_row["worm_object_id"] is None
    assert protegido_audit_count == 0

    ok, broken_id = await audit.verify_chain(conn, slug)
    assert ok is True
    assert broken_id is None


# ---------------------------------------------------------------------------
# (c) Idempotencia REAL: dos llamadas SECUENCIALES sobre el mismo índice —
# la segunda ni siquiera reintenta la llamada a storage-service.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_proteger_worm_indice_second_call_is_idempotent(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    expediente_id, row = await _seed_expediente_firmado(conn, slug, "itest worm idempotente")
    index = IndexService(conn, slug)

    with patch(
        "app.clients.storage_client.proteger_indice_worm_internal",
        new=AsyncMock(return_value=_ok_outcome()),
    ) as mock_client:
        primero = await index.proteger_worm_indice(
            expediente_id, row["id"], actor_id=None, origen="reconciliation"
        )
        segundo = await index.proteger_worm_indice(
            expediente_id, row["id"], actor_id=None, origen="reconciliation"
        )

    assert primero == "protegido"
    assert segundo == "omitido"
    assert mock_client.await_count == 1  # la 2da llamada ni toca storage-service

    protegido_audit_count = await conn.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
        slug,
        "archive.indice_worm_protegido",
    )
    assert protegido_audit_count == 1


# ---------------------------------------------------------------------------
# (d) Idempotencia bajo CONCURRENCIA real: dos corrutinas, cada una con su
# propia conexión asyncpg, disparando `proteger_worm_indice` sobre el MISMO
# índice al mismo tiempo — solo una debe ganar `mark_worm_protegido`.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_concurrent_proteger_worm_indice_does_not_double_mark(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    expediente_id, row = await _seed_expediente_firmado(conn, slug, "itest worm concurrente")

    async def _attempt() -> str:
        own_conn = await asyncpg.connect(dsn=DSN)
        try:
            await own_conn.execute(f'SET search_path = "{schema}", public')
            with patch(
                "app.clients.storage_client.proteger_indice_worm_internal",
                new=AsyncMock(return_value=_ok_outcome()),
            ):
                return await IndexService(own_conn, slug).proteger_worm_indice(
                    expediente_id, row["id"], actor_id=None, origen="reconciliation"
                )
        finally:
            await own_conn.close()

    # Sin sleeps: ambas corrutinas compiten de verdad por el MISMO UPDATE
    # (WHERE id=$1 AND worm_protegido_at IS NULL) sobre la misma fila.
    resultados = await asyncio.gather(_attempt(), _attempt())

    assert "protegido" in resultados
    assert set(resultados) <= {"protegido", "omitido"}

    verify_conn = await asyncpg.connect(dsn=DSN)
    try:
        protegido_audit_count = await verify_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.indice_worm_protegido",
        )
        indice_row = await verify_conn.fetchrow(
            f'SELECT worm_protegido_at FROM "{schema}".expediente_indice WHERE id = $1',
            row["id"],
        )
    finally:
        await verify_conn.close()

    # CRÍTICO: solo UN asiento real, aunque ambas corrutinas hayan recibido
    # `ok=True` del cliente storage (mockeado, no compite de verdad) — el
    # candado real es el `WHERE worm_protegido_at IS NULL` del UPDATE.
    assert protegido_audit_count == 1
    assert indice_row["worm_protegido_at"] is not None

    ok, broken_id = await audit.verify_chain(conn, slug)
    assert ok is True
    assert broken_id is None


# ---------------------------------------------------------------------------
# (e) Derivación de retención fail-closed contra datos TRD REALES.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_proteger_worm_indice_fail_closed_without_trd_retention_years(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Serie TRD real SIN `archivo_gestion_years`/`archivo_central_years`
    declarados (ambos NULL) — el cableado WORM NUNCA se dispara con una
    retención inventada; queda 'pendiente' con el motivo correcto, elegible
    para que `run_once_worm` reintente si un archivista corrige la TRD."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    expediente_id, row = await _seed_expediente_firmado(
        conn,
        slug,
        "itest worm sin retencion TRD",
        archivo_gestion_years=None,
        archivo_central_years=None,
    )

    resultado = await IndexService(conn, slug).proteger_worm_indice(
        expediente_id, row["id"], actor_id=None, origen="reconciliation"
    )

    assert resultado == "pendiente"
    indice_row = await conn.fetchrow(
        f'SELECT worm_protegido_at, worm_reconcile_intentos, worm_reconcile_ultimo_error, '
        f'worm_reconcile_proximo_at FROM "{schema}".expediente_indice WHERE id = $1',
        row["id"],
    )
    assert indice_row["worm_protegido_at"] is None
    assert indice_row["worm_reconcile_intentos"] == 1
    assert indice_row["worm_reconcile_ultimo_error"] == "trd_sin_retencion"
    assert indice_row["worm_reconcile_proximo_at"] is not None


# ---------------------------------------------------------------------------
# (f) `IndexRepository.list_worm_pending` — filtro SQL real de backoff y
# agotamiento (barrido de `run_once_worm`).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_list_worm_pending_respects_backoff_and_agotado(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    repo = IndexRepository(conn)

    _, elegible = await _seed_expediente_firmado(conn, slug, "itest worm elegible")
    _, con_backoff_futuro = await _seed_expediente_firmado(conn, slug, "itest worm backoff futuro")
    _, agotado = await _seed_expediente_firmado(conn, slug, "itest worm agotado")
    _, ya_protegido = await _seed_expediente_firmado(conn, slug, "itest worm ya protegido")

    await conn.execute(
        f'UPDATE "{schema}".expediente_indice SET worm_reconcile_proximo_at = now() + interval \'1 day\' '
        "WHERE id = $1",
        con_backoff_futuro["id"],
    )
    await conn.execute(
        f'UPDATE "{schema}".expediente_indice SET worm_reconcile_agotado = TRUE WHERE id = $1',
        agotado["id"],
    )
    await conn.execute(
        f'UPDATE "{schema}".expediente_indice SET worm_protegido_at = now(), worm_object_id = $2 '
        "WHERE id = $1",
        ya_protegido["id"],
        uuid.uuid4(),
    )

    pending = await repo.list_worm_pending(50)
    pending_ids = {r["id"] for r in pending}

    assert elegible["id"] in pending_ids
    assert con_backoff_futuro["id"] not in pending_ids
    assert agotado["id"] not in pending_ids
    assert ya_protegido["id"] not in pending_ids
