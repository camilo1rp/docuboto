# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Cableado WORM/Object-Lock del ACTA de transferencia (E10 Inc.2 wiring)
contra Postgres REAL: atomicidad marca<->asiento de
`TransferenciaService.proteger_worm_acta` (commit conjunto, rollback
conjunto ante un fallo tras `audit.append`), idempotencia bajo dos llamadas
reales (secuenciales y concurrentes) sobre la MISMA acta, derivación de
retención fail-closed contra datos TRD reales sin años declarados, y el
filtro SQL de `TransferenciaRepository.list_worm_pending_actas`
(backoff/agotamiento). Espejo EXACTO de
`tests/integration/test_indice_worm_atomicity.py` (E10 Inc.1, índice) para
su gemela del acta.

Compromiso (igual que el resto de `tests/integration/`): NO hay tráfico HTTP
real a storage-service -- `storage_client.proteger_artefacto_worm_internal`
se monkeypatchea a un `WormProtectionOutcome` fijo. Lo que este tier prueba
es el tramo 100%-DB (UPDATE + INSERT audit_log atómicos, WHERE de
idempotencia, derivación TRD), no la subida real a MinIO (cubierta en
storage-service).

Seed: la transferencia se recibe REAL (`TransferenciaService.recibir`, sin
mocks de BD -- el sellado síncrono post-commit del acta lo no-opea
`tests/integration/conftest.py::_no_real_http_acta_sign`, autouse en todo
este tier), dejando el acta 'pendiente_firma'; este seed la promueve a
'firmado' con un UPDATE directo (mismo atajo que `test_indice_worm_atomicity
.py` usa para el índice: sin sellado XAdES real ni tráfico HTTP a
signature-service en este tier).
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from orpycamcp_common import audit

from app.clients.storage_client import WormProtectionOutcome
from app.core.actor import Actor
from app.core.database import tenant_schema
from app.repositories.transferencia_repository import TransferenciaRepository
from app.schemas.archive import ExpedienteCreate, TrdSerieCreate
from app.schemas.transferencia import TransferenciaCreate
from app.services.archive_service import ArchiveService
from app.services.transferencia_service import TransferenciaService

from .conftest import DSN, convalidate_for_test


async def _seed_acta_firmada(
    conn: asyncpg.Connection,
    slug: str,
    name: str,
    *,
    archivo_gestion_years: int | None = 5,
    archivo_central_years: int | None = 5,
) -> tuple[uuid.UUID, asyncpg.Record]:
    """Recibe una transferencia REAL (sin mocks de BD) sobre un expediente con
    una serie TRD real (retención declarada por parámetro) y promueve su acta
    a 'firmado' directamente (sin signature-service real, ver docstring del
    módulo). Devuelve ``(transferencia_id, acta_firma_row)``."""
    schema = tenant_schema(slug)
    archive = ArchiveService(conn, slug)
    serie = await archive.create_trd_serie(
        TrdSerieCreate(
            code=f"WRM{uuid.uuid4().hex[:6]}",
            name="itest serie worm acta",
            # Mismo motivo que `test_indice_worm_atomicity.py`: anula también
            # el fallback retention_years/total_retention para el escenario
            # "TRD sin retención declarada" (total derivado real = 0).
            retention_years=0,
            total_retention=0,
            archivo_gestion_years=archivo_gestion_years,
            archivo_central_years=archivo_central_years,
        ),
        actor=None,
    )
    # ADR-025 (Increment 1): la serie nace en 'borrador' -- no gobierna el
    # cierre de más abajo (D5) sin pasar antes por una versión vigente.
    # 'migrada' es el atajo de test correcto (D6).
    await convalidate_for_test(conn, serie.id)
    created = await archive.create_expediente(
        payload=ExpedienteCreate(name=name, nivel_seguridad=1, trd_serie_id=serie.id),
        opened_by=None,
    )
    await archive.close_expediente(created.id)  # actor_id=None -> índice 'pendiente_firma'
    await conn.execute(
        f'UPDATE "{schema}".expediente_indice SET estado = \'firmado\', firma_id = $2 '
        "WHERE expediente_id = $1 AND estado <> 'firmado'",
        created.id,
        uuid.uuid4(),
    )

    actor = Actor(id=uuid.uuid4(), username="itest-seed")
    transferencia = TransferenciaService(conn, slug)
    resp = await transferencia.create(
        TransferenciaCreate(tipo="primaria", expediente_id=created.id, objeto="Transferencia primaria de la serie de contratos"), actor
    )
    await transferencia.enviar(resp.id, actor)
    # `_sellar_acta_post_commit` está no-opeado por el autouse
    # `_no_real_http_acta_sign` de este tier -- el acta queda 'pendiente_firma'
    # tal cual la deja `recibir()`.
    await transferencia.recibir(resp.id, actor)

    file_id = uuid.uuid4()
    row = await conn.fetchrow(
        f"""
        UPDATE "{schema}".transferencia_acta_firma
        SET estado = 'firmado', firma_id = $2, minio_key_firmado = $3,
            xades_level = 'XAdES-T', acreditado = false, firmado_at = now()
        WHERE transferencia_id = $1 AND estado <> 'firmado'
        RETURNING transferencia_id, minio_key_firmado, worm_reconcile_intentos
        """,
        resp.id,
        uuid.uuid4(),
        f"{file_id}/acta-{resp.id}.xml",
    )
    assert row is not None
    return resp.id, row


def _ok_outcome() -> WormProtectionOutcome:
    return WormProtectionOutcome(ok=True, worm_object_id=uuid.uuid4())


# ---------------------------------------------------------------------------
# (a) Atomicidad marca<->asiento: commit conjunto en el camino feliz.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_proteger_worm_acta_commits_mark_and_audit_together(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    transferencia_id, row = await _seed_acta_firmada(conn, slug, "itest acta worm OK")
    outcome = _ok_outcome()

    with patch(
        "app.clients.storage_client.proteger_artefacto_worm_internal",
        new=AsyncMock(return_value=outcome),
    ):
        resultado = await TransferenciaService(conn, slug).proteger_worm_acta(
            transferencia_id, actor_id=None, origen="reconciliation"
        )

    assert resultado == "protegido"

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        firma_row = await second_conn.fetchrow(
            f'SELECT worm_protegido_at, worm_object_id, worm_reconcile_intentos '
            f'FROM "{schema}".transferencia_acta_firma WHERE transferencia_id = $1',
            transferencia_id,
        )
        audit_row = await second_conn.fetchrow(
            "SELECT actor, canal, payload FROM public.audit_log "
            "WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.acta_worm_protegido",
        )
    finally:
        await second_conn.close()

    assert firma_row["worm_protegido_at"] is not None
    assert firma_row["worm_object_id"] == outcome.worm_object_id
    assert audit_row is not None
    assert audit_row["canal"] == "system"

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (b) Atomicidad marca<->asiento: fallo DESPUÉS de audit.append revierte
# ambos, aunque `proteger_worm_acta` se trague la excepción (best-effort
# real: nunca propaga, pero tampoco deja el marcado a medias).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_proteger_worm_acta_failure_after_audit_append_reverts_mark(
    tenant_ctx: tuple[str, asyncpg.Connection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    transferencia_id, row = await _seed_acta_firmada(conn, slug, "itest acta worm boom")

    original_append = audit.append

    async def _append_then_boom(*args: object, **kwargs: object) -> str:
        await original_append(*args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError("boom-post-audit (acta worm, b2 fault injection)")

    monkeypatch.setattr(audit, "append", _append_then_boom)

    with patch(
        "app.clients.storage_client.proteger_artefacto_worm_internal",
        new=AsyncMock(return_value=_ok_outcome()),
    ):
        resultado = await TransferenciaService(conn, slug).proteger_worm_acta(
            transferencia_id, actor_id=None, origen="reconciliation"
        )

    # Best-effort real: NUNCA propaga la excepción al llamador...
    assert resultado == "pendiente"

    # ...pero el UPDATE revirtió JUNTO con el asiento que sí llegó a
    # ejecutarse (dentro del SAVEPOINT de audit.append) antes de que la
    # excepción reventara la tx externa de `proteger_worm_acta`.
    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        firma_row = await second_conn.fetchrow(
            f'SELECT worm_protegido_at, worm_object_id FROM '
            f'"{schema}".transferencia_acta_firma WHERE transferencia_id = $1',
            transferencia_id,
        )
        protegido_audit_count = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.acta_worm_protegido",
        )
    finally:
        await second_conn.close()

    assert firma_row["worm_protegido_at"] is None
    assert firma_row["worm_object_id"] is None
    assert protegido_audit_count == 0

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (c) Idempotencia REAL: dos llamadas SECUENCIALES sobre la misma acta -- la
# segunda ni siquiera reintenta la llamada a storage-service.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_proteger_worm_acta_second_call_is_idempotent(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    transferencia_id, row = await _seed_acta_firmada(conn, slug, "itest acta worm idempotente")
    service = TransferenciaService(conn, slug)

    with patch(
        "app.clients.storage_client.proteger_artefacto_worm_internal",
        new=AsyncMock(return_value=_ok_outcome()),
    ) as mock_client:
        primero = await service.proteger_worm_acta(
            transferencia_id, actor_id=None, origen="reconciliation"
        )
        segundo = await service.proteger_worm_acta(
            transferencia_id, actor_id=None, origen="reconciliation"
        )

    assert primero == "protegido"
    assert segundo == "omitido"
    assert mock_client.await_count == 1  # la 2da llamada ni toca storage-service

    protegido_audit_count = await conn.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
        slug,
        "archive.acta_worm_protegido",
    )
    assert protegido_audit_count == 1


# ---------------------------------------------------------------------------
# (d) Idempotencia bajo CONCURRENCIA real: dos corrutinas, cada una con su
# propia conexión asyncpg, disparando `proteger_worm_acta` sobre la MISMA
# acta al mismo tiempo -- solo una debe ganar `mark_acta_worm_protegido`.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_concurrent_proteger_worm_acta_does_not_double_mark(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    transferencia_id, row = await _seed_acta_firmada(conn, slug, "itest acta worm concurrente")

    async def _attempt() -> str:
        own_conn = await asyncpg.connect(dsn=DSN)
        try:
            await own_conn.execute(f'SET search_path = "{schema}", public')
            return await TransferenciaService(own_conn, slug).proteger_worm_acta(
                transferencia_id, actor_id=None, origen="reconciliation"
            )
        finally:
            await own_conn.close()

    # Sin sleeps: ambas corrutinas compiten de verdad por el MISMO UPDATE
    # (WHERE transferencia_id=$1 AND worm_protegido_at IS NULL) sobre la
    # misma fila. UN solo patch que envuelve el gather: un `with patch` dentro de cada
    # corrutina sale en orden no-LIFO y restaura el mock de la otra, dejándolo fugado
    # (rompía `test_clientes_llevan_token_interno` más adelante en la misma sesión).
    with patch(
        "app.clients.storage_client.proteger_artefacto_worm_internal",
        new=AsyncMock(return_value=_ok_outcome()),
    ):
        resultados = await asyncio.gather(_attempt(), _attempt())

    assert "protegido" in resultados
    assert set(resultados) <= {"protegido", "omitido"}

    verify_conn = await asyncpg.connect(dsn=DSN)
    try:
        protegido_audit_count = await verify_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.acta_worm_protegido",
        )
        firma_row = await verify_conn.fetchrow(
            f'SELECT worm_protegido_at FROM "{schema}".transferencia_acta_firma '
            "WHERE transferencia_id = $1",
            transferencia_id,
        )
    finally:
        await verify_conn.close()

    # CRÍTICO: solo UN asiento real, aunque ambas corrutinas hayan recibido
    # `ok=True` del cliente storage (mockeado, no compite de verdad) -- el
    # candado real es el `WHERE worm_protegido_at IS NULL` del UPDATE.
    assert protegido_audit_count == 1
    assert firma_row["worm_protegido_at"] is not None

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (e) Derivación de retención fail-closed contra datos TRD REALES.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_proteger_worm_acta_fail_closed_without_trd_retention_years(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Serie TRD real SIN `archivo_gestion_years`/`archivo_central_years`
    declarados (ambos NULL) -- el cableado WORM del acta NUNCA se dispara con
    una retención inventada; queda 'pendiente' con el motivo correcto,
    elegible para que `run_once_actas_worm` reintente si un archivista
    corrige la TRD."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    transferencia_id, row = await _seed_acta_firmada(
        conn,
        slug,
        "itest acta worm sin retencion TRD",
        archivo_gestion_years=None,
        archivo_central_years=None,
    )

    resultado = await TransferenciaService(conn, slug).proteger_worm_acta(
        transferencia_id, actor_id=None, origen="reconciliation"
    )

    assert resultado == "pendiente"
    firma_row = await conn.fetchrow(
        f'SELECT worm_protegido_at, worm_reconcile_intentos, worm_reconcile_ultimo_error, '
        f'worm_reconcile_proximo_at FROM "{schema}".transferencia_acta_firma '
        "WHERE transferencia_id = $1",
        transferencia_id,
    )
    assert firma_row["worm_protegido_at"] is None
    assert firma_row["worm_reconcile_intentos"] == 1
    assert firma_row["worm_reconcile_ultimo_error"] == "trd_sin_retencion"
    assert firma_row["worm_reconcile_proximo_at"] is not None


# ---------------------------------------------------------------------------
# (f) `TransferenciaRepository.list_worm_pending_actas` -- filtro SQL real de
# backoff y agotamiento (barrido de `run_once_actas_worm`).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_list_worm_pending_actas_respects_backoff_and_agotado(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    repo = TransferenciaRepository(conn, slug)

    elegible_id, _ = await _seed_acta_firmada(conn, slug, "itest acta worm elegible")
    backoff_futuro_id, _ = await _seed_acta_firmada(conn, slug, "itest acta worm backoff futuro")
    agotado_id, _ = await _seed_acta_firmada(conn, slug, "itest acta worm agotado")
    ya_protegido_id, _ = await _seed_acta_firmada(conn, slug, "itest acta worm ya protegido")

    await conn.execute(
        f'UPDATE "{schema}".transferencia_acta_firma '
        "SET worm_reconcile_proximo_at = now() + interval '1 day' WHERE transferencia_id = $1",
        backoff_futuro_id,
    )
    await conn.execute(
        f'UPDATE "{schema}".transferencia_acta_firma SET worm_reconcile_agotado = TRUE '
        "WHERE transferencia_id = $1",
        agotado_id,
    )
    await conn.execute(
        f'UPDATE "{schema}".transferencia_acta_firma '
        "SET worm_protegido_at = now(), worm_object_id = $2 WHERE transferencia_id = $1",
        ya_protegido_id,
        uuid.uuid4(),
    )

    pending = await repo.list_worm_pending_actas(50)
    pending_ids = {r["transferencia_id"] for r in pending}

    assert elegible_id in pending_ids
    assert backoff_futuro_id not in pending_ids
    assert agotado_id not in pending_ids
    assert ya_protegido_id not in pending_ids


# ---------------------------------------------------------------------------
# (g) Migración 030 -- idempotencia REAL (re-ejecución contra Postgres): el
# schema del `tenant_ctx` ya aplicó esta migración una vez durante el
# provisioning (`migrations/runner.py`); re-emitirla una SEGUNDA vez sobre el
# MISMO schema no debe fallar (`ADD COLUMN IF NOT EXISTS` + `CREATE INDEX IF
# NOT EXISTS`, sin ninguna sentencia que asuma estado previo sin
# comprobarlo) y las columnas/índice deben seguir sirviendo el resto del
# cableado con normalidad.
# ---------------------------------------------------------------------------

_MIGRATION_030_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "migrations"
    / "tenant"
    / "030_transferencia_acta_worm.sql"
)


@pytest.mark.integration
async def test_migration_030_is_idempotent_on_real_postgres(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    sql = _MIGRATION_030_PATH.read_text(encoding="utf-8")

    # Re-ejecuta la migración completa una SEGUNDA vez sobre el schema ya
    # migrado por `tenant_ctx` -- no debe lanzar ninguna excepción.
    async with conn.transaction():
        await conn.execute(f'SET LOCAL search_path = "{schema}", public')
        await conn.execute(sql)

    # El cableado sigue funcionando con normalidad tras la re-ejecución: se
    # puede sembrar y proteger una acta como en cualquier otro test de este
    # módulo.
    transferencia_id, row = await _seed_acta_firmada(conn, slug, "itest migracion 030 idempotente")

    with patch(
        "app.clients.storage_client.proteger_artefacto_worm_internal",
        new=AsyncMock(return_value=_ok_outcome()),
    ):
        resultado = await TransferenciaService(conn, slug).proteger_worm_acta(
            transferencia_id, actor_id=None, origen="reconciliation"
        )

    assert resultado == "protegido"
