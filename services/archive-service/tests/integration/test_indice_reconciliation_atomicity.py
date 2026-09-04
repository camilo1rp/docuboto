# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Reconciliación del sellado del índice (residual Ac. AGN 001/2024 art.
4.3.2.4) contra Postgres REAL: atomicidad efecto<->asiento de
`_reconciliar_indice` (b2-style, fallo tras `audit.append` real), idempotencia
bajo dos pasadas concurrentes del mismo índice pendiente (`mark_signed WHERE
estado <> 'firmado'`, el advisory lock del job es una optimización, NO la
única defensa) y que un índice AGOTADO permanece `pendiente_firma` de forma
permanente (nunca inventa conformidad).

Compromiso (igual que el resto de `tests/integration/`): NO hay tráfico HTTP
real a signature-service — `signature_client.sign_indice_internal` se
monkeypatchea a un `SignOutcome` fijo. Lo que este tier prueba es el tramo
100%-DB (UPDATE + INSERT audit_log atómicos, WHERE de idempotencia, backoff),
no el sellado XAdES-B en sí (cubierto en signature-service).

Seed: `ArchiveService.close_expediente(..., actor_id=None)` deja el índice
'pendiente_firma' de forma 100% real (sin mocks) — `actor_id=None` hace que
`IndexService.firmar_indice_close` corte ANTES de intentar cualquier llamada
HTTP (motivo='sin_actor_id'), exactamente el mismo atajo que ya usa
`test_atomicity.py` para no depender de signature-service real.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from fastapi import HTTPException
from orpycamcp_common import audit

from app.clients.signature_client import SignedIndice, SignOutcome
from app.core.database import tenant_schema
from app.jobs.indice_reconciliation import _reconciliar_indice
from app.repositories.index_repository import IndexRepository
from app.schemas.archive import ExpedienteCreate
from app.services.archive_service import ArchiveService
from app.services.index_service import IndexService

from .conftest import DSN


def _fake_signed() -> SignedIndice:
    return SignedIndice(
        firma_id=uuid.uuid4(),
        minio_key_firmado="file-id/indice-reconciliado.xml",
        nivel_conformidad="xades_b_no_acreditado",
        xades_level="XAdES-T",
        tipo_firma="sello_sistema",
        acreditado=False,
        certificado_fingerprint="itest:fingerprint",
    )


async def _seed_pendiente_firma(conn: asyncpg.Connection, slug: str, name: str):
    """Deja un expediente 'closed' con su índice vigente 'pendiente_firma'
    (real, sin mocks — ver docstring del módulo). Devuelve
    ``(expediente_id, indice_row)`` donde ``indice_row`` es la fila que
    ``IndexRepository.list_pending_signature`` produciría."""
    service = ArchiveService(conn, slug)
    created = await service.create_expediente(
        payload=ExpedienteCreate(name=name, nivel_seguridad=1), opened_by=None
    )
    await service.close_expediente(created.id)  # actor_id=None -> pendiente_firma
    rows = await IndexRepository(conn).list_pending_signature(10)
    (row,) = [r for r in rows if r["expediente_id"] == created.id]
    return created.id, row


# ---------------------------------------------------------------------------
# (a) Atomicidad efecto<->asiento de _reconciliar_indice: fallo DESPUÉS de
# audit.append, dentro de la MISMA transacción — mismo patrón b2 que
# test_atomicity.py.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_reconciliar_indice_failure_after_audit_append_reverts_mark_signed(
    tenant_ctx: tuple[str, asyncpg.Connection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    expediente_id, row = await _seed_pendiente_firma(conn, slug, "itest reconcile boom")

    original_append = audit.append

    async def _append_then_boom(*args: object, **kwargs: object) -> str:
        await original_append(*args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError("boom-post-audit (reconciliacion, b2 fault injection)")

    monkeypatch.setattr(audit, "append", _append_then_boom)

    with patch(
        "app.jobs.indice_reconciliation.signature_client.sign_indice_internal",
        new=AsyncMock(return_value=SignOutcome(signed=_fake_signed())),
    ):
        with pytest.raises(RuntimeError, match="boom-post-audit"):
            await _reconciliar_indice(conn, slug, row)

    # Verificación post-rollback desde una SEGUNDA conexión.
    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        estado = await second_conn.fetchval(
            f'SELECT estado FROM "{schema}".expediente_indice WHERE id = $1', row["id"]
        )
        firmado_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.indice_firmado",
        )
    finally:
        await second_conn.close()

    # El UPDATE a 'firmado' revirtió JUNTO con el asiento que sí llegó a
    # ejecutarse (INSERT real, dentro del SAVEPOINT de audit.append) antes de
    # que la excepción reventara la tx externa de _reconciliar_indice.
    assert estado == "pendiente_firma"
    assert firmado_audit == 0

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


@pytest.mark.integration
async def test_reconciliar_indice_commits_signed_and_audit_together(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    expediente_id, row = await _seed_pendiente_firma(conn, slug, "itest reconcile OK")
    signed = _fake_signed()

    with patch(
        "app.jobs.indice_reconciliation.signature_client.sign_indice_internal",
        new=AsyncMock(return_value=SignOutcome(signed=signed)),
    ):
        resultado = await _reconciliar_indice(conn, slug, row)

    assert resultado == "sellado"

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        indice_row = await second_conn.fetchrow(
            f'SELECT estado, firma_id, reconcile_intentos, reconcile_agotado '
            f'FROM "{schema}".expediente_indice WHERE id = $1',
            row["id"],
        )
        audit_row = await second_conn.fetchrow(
            "SELECT actor, canal, payload FROM public.audit_log "
            "WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.indice_firmado",
        )
    finally:
        await second_conn.close()

    assert indice_row["estado"] == "firmado"
    assert indice_row["firma_id"] == signed.firma_id
    # mark_signed resetea el backoff al firmarse (migración 025).
    assert indice_row["reconcile_intentos"] == 0
    assert indice_row["reconcile_agotado"] is False

    assert audit_row is not None
    assert audit_row["canal"] == "system"
    from app.core.constants import SYSTEM_RECONCILER_ID

    assert audit_row["actor"] == str(SYSTEM_RECONCILER_ID)
    payload = json.loads(audit_row["payload"])
    assert payload["origen"] == "reconciliation"

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (b) Idempotencia REAL: dos pasadas concurrentes de _reconciliar_indice
# sobre el MISMO índice pendiente, cada una con su propia conexión asyncpg —
# `mark_signed WHERE estado <> 'firmado'` debe garantizar que solo UNA
# comprometa el sellado, aunque ambas reciban un SignOutcome de éxito (p. ej.
# dos instancias del job que perdieron la carrera del advisory lock por un
# instante, o un reintento manual solapado con el job).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_concurrent_reconciliar_indice_does_not_double_sign(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    expediente_id, row = await _seed_pendiente_firma(conn, slug, "itest reconcile concurrente")

    async def _attempt() -> str:
        own_conn = await asyncpg.connect(dsn=DSN)
        try:
            await own_conn.execute(f'SET search_path = "{schema}", public')
            with patch(
                "app.jobs.indice_reconciliation.signature_client.sign_indice_internal",
                new=AsyncMock(return_value=SignOutcome(signed=_fake_signed())),
            ):
                return await _reconciliar_indice(own_conn, slug, row)
        finally:
            await own_conn.close()

    # Sin sleeps: ambas corrutinas compiten de verdad por el MISMO UPDATE
    # (WHERE id=$1 AND estado <> 'firmado') sobre la misma fila.
    resultados = await asyncio.gather(_attempt(), _attempt())

    assert set(resultados) == {"sellado"}  # ambas ramas creen haber "sellado"...

    verify_conn = await asyncpg.connect(dsn=DSN)
    try:
        # ...pero solo UN asiento 'archive.indice_firmado' de reconciliación
        # quedó realmente comprometido — el UPDATE de la segunda corrutina no
        # casó ninguna fila (ya 'firmado'), así que su firma_id/actor no
        # "ganó" la columna, y CRÍTICO: solo una escritura de auditoría real.
        firmado_audit_count = await verify_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.indice_firmado",
        )
        estado = await verify_conn.fetchval(
            f'SELECT estado FROM "{schema}".expediente_indice WHERE id = $1', row["id"]
        )
    finally:
        await verify_conn.close()

    assert firmado_audit_count == 1
    assert estado == "firmado"

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (b2) S1 (RF-FIR-14): carrera REAL entre el reintento MANUAL
# (`IndexService.firmar_indice`, `POST /indice/firmar`) y el job de
# reconciliación (`_reconciliar_indice`) sobre el MISMO índice pendiente —
# exactamente el escenario del hallazgo de auditoría: solo UNA de las dos
# rutas debe ganar `mark_signed` y solo esa debe dejar su asiento
# `archive.indice_firmado`; la otra pierde la carrera SIN escribir un asiento
# fantasma (manual: 409 idempotente `indice_ya_firmado`; job: sigue
# reportando 'sellado' -- ver `_reconciliar_indice`, comportamiento
# best-effort del job sin cambios).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_manual_retry_vs_job_race_does_not_write_phantom_audit(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    expediente_id, row = await _seed_pendiente_firma(conn, slug, "itest carrera manual-vs-job")
    actor_id = str(uuid.uuid4())

    async def _manual_attempt() -> object:
        own_conn = await asyncpg.connect(dsn=DSN)
        try:
            await own_conn.execute(f'SET search_path = "{schema}", public')
            with patch(
                "app.clients.signature_client.sign_xml",
                new=AsyncMock(return_value=_fake_signed()),
            ):
                svc = IndexService(own_conn, slug)
                return await svc.firmar_indice(
                    expediente_id, tenant_slug=slug, actor_id=actor_id
                )
        finally:
            await own_conn.close()

    async def _job_attempt() -> str:
        own_conn = await asyncpg.connect(dsn=DSN)
        try:
            await own_conn.execute(f'SET search_path = "{schema}", public')
            with patch(
                "app.jobs.indice_reconciliation.signature_client.sign_indice_internal",
                new=AsyncMock(return_value=SignOutcome(signed=_fake_signed())),
            ):
                return await _reconciliar_indice(own_conn, slug, row)
        finally:
            await own_conn.close()

    # Sin sleeps: ambas corrutinas compiten de verdad por el MISMO UPDATE
    # (WHERE id=$1 AND estado <> 'firmado') sobre la misma fila. `firmar_indice`
    # (manual) lanza HTTPException 409 si pierde -- se captura como resultado
    # en vez de abortar `gather`.
    manual_result, job_result = await asyncio.gather(
        _manual_attempt(), _job_attempt(), return_exceptions=True
    )

    assert job_result == "sellado"  # el job SIEMPRE reporta 'sellado' si obtuvo un signed
    if isinstance(manual_result, HTTPException):
        assert manual_result.status_code == 409
        assert manual_result.detail["error"] == "indice_ya_firmado"
    else:
        assert not isinstance(manual_result, BaseException)
        assert manual_result is not None  # el manual ganó la carrera

    verify_conn = await asyncpg.connect(dsn=DSN)
    try:
        firmado_audit_count = await verify_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.indice_firmado",
        )
        estado = await verify_conn.fetchval(
            f'SELECT estado FROM "{schema}".expediente_indice WHERE id = $1', row["id"]
        )
    finally:
        await verify_conn.close()

    # Exactamente UN asiento -- ninguna rama duplicó ni omitió el suyo.
    assert firmado_audit_count == 1
    assert estado == "firmado"

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (c) Índice AGOTADO permanece 'pendiente_firma' — nunca inventa conformidad.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_reconciliar_indice_agotado_permanece_pendiente_firma(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "indice_reconciliation_max_intentos", 1)
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    expediente_id, row = await _seed_pendiente_firma(conn, slug, "itest reconcile agotado")

    with patch(
        "app.jobs.indice_reconciliation.signature_client.sign_indice_internal",
        new=AsyncMock(return_value=SignOutcome(signed=None, transient=True)),
    ):
        resultado = await _reconciliar_indice(conn, slug, row)

    assert resultado == "agotado"

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        indice_row = await second_conn.fetchrow(
            f'SELECT estado, reconcile_agotado, reconcile_intentos, reconcile_proximo_at '
            f'FROM "{schema}".expediente_indice WHERE id = $1',
            row["id"],
        )
        agotado_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.indice_reconcile_agotado",
        )
    finally:
        await second_conn.close()

    # NUNCA 'firmado' — permanece pendiente, requiere intervención manual.
    assert indice_row["estado"] == "pendiente_firma"
    assert indice_row["reconcile_agotado"] is True
    assert indice_row["reconcile_intentos"] == 1
    assert indice_row["reconcile_proximo_at"] is None
    assert agotado_audit == 1

    # Un índice agotado ya NO debe aparecer en la próxima barrida.
    pending_after = await IndexRepository(conn).list_pending_signature(10)
    assert row["id"] not in {r["id"] for r in pending_after}

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# ---------------------------------------------------------------------------
# (d) S2: atomicidad efecto<->asiento de `bump_intento` + su asiento
# 'archive.indice_reconcile_agotado' -- mismo patrón b2 que (a) arriba, pero
# para la rama AGOTADO en vez de la rama SELLADO.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_reconciliar_indice_agotado_failure_after_audit_append_reverts_bump_intento(
    tenant_ctx: tuple[str, asyncpg.Connection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S2: si el proceso revienta DESPUÉS de `audit.append` pero DENTRO de la
    misma transacción de la rama `agotado`, el `bump_intento` (que marcaría
    `reconcile_agotado=TRUE`) debe revertir JUNTO con el asiento -- antes eran
    dos commits autocommit separados y esta fault-injection habría dejado
    `reconcile_agotado=TRUE` con CERO asientos que lo explicaran."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "indice_reconciliation_max_intentos", 1)
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    expediente_id, row = await _seed_pendiente_firma(conn, slug, "itest agotado boom")

    original_append = audit.append

    async def _append_then_boom(*args: object, **kwargs: object) -> str:
        await original_append(*args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError("boom-post-audit (agotado, b2 fault injection)")

    monkeypatch.setattr(audit, "append", _append_then_boom)

    with patch(
        "app.jobs.indice_reconciliation.signature_client.sign_indice_internal",
        new=AsyncMock(return_value=SignOutcome(signed=None, transient=True)),
    ):
        with pytest.raises(RuntimeError, match="boom-post-audit"):
            await _reconciliar_indice(conn, slug, row)

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        indice_row = await second_conn.fetchrow(
            f'SELECT reconcile_agotado, reconcile_intentos, reconcile_ultimo_error '
            f'FROM "{schema}".expediente_indice WHERE id = $1',
            row["id"],
        )
        agotado_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.indice_reconcile_agotado",
        )
    finally:
        await second_conn.close()

    # El UPDATE de `bump_intento` revirtió JUNTO con el asiento -- el índice
    # queda EXACTAMENTE como lo dejó el seed (`_seed_pendiente_firma`,
    # motivo='sin_actor_id' del cierre sin actor -- ver su docstring), nunca
    # en el estado intermedio inconsistente (agotado sin asiento que lo
    # explique).
    assert indice_row["reconcile_agotado"] is False
    assert indice_row["reconcile_intentos"] == 0
    assert indice_row["reconcile_ultimo_error"] == "sin_actor_id"
    assert agotado_audit == 0

    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None
