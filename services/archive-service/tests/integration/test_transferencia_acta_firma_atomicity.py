# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Firma XAdES del acta de transferencia (H-H, E06/F4) contra Postgres REAL:
- (a) `insert_acta_firma_pendiente` es ATÓMICA con `insert_acta`/`recibir()`:
  TODA acta recibida tiene su fila `transferencia_acta_firma`, incluso cuando
  el sellado síncrono post-commit nunca llega a correr (proceso muerto justo
  tras el commit).
- (b) atomicidad efecto<->asiento de `mark_acta_firmada` <-> `archive.acta_firmada`
  (b2-style, fallo tras `audit.append` real, gemela de
  `test_indice_reconciliation_atomicity.py`).
- (c) idempotencia REAL bajo carrera manual (`firmar_acta_retry`) vs. job
  (`_reconciliar_acta`) sobre la MISMA acta pendiente.
- (d) acta AGOTADA permanece 'pendiente_firma' -- nunca inventa conformidad.

Compromiso (igual que el resto de `tests/integration/`): NO hay tráfico HTTP
real a signature-service -- `signature_client.sign_xml_outcome`/
`sign_indice_internal` se monkeypatchean. Lo que este tier prueba es el tramo
100%-DB (INSERT/UPDATE + audit_log atómicos, WHERE de idempotencia, backoff);
el sellado XAdES real y que el digest del XML firmado cubre exactamente el
`acta_fuid_xml` congelado se prueba en signature-service
(`tests/test_acta_transferencia_signing.py`, round-trip sign+verify real).
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
from app.core.actor import Actor
from app.core.database import tenant_schema
from app.jobs.indice_reconciliation import _reconciliar_acta
from app.repositories.transferencia_repository import TransferenciaRepository
from app.services.transferencia_service import TransferenciaService

from .conftest import DSN
from .test_atomicity import _seed_transferencia_lista_para_recibir

# `tests/integration/conftest.py::_no_real_http_acta_sign` (autouse) no-opea
# `TransferenciaService._sellar_acta_post_commit` para TODO el tier (evita
# HTTP real a signature-service en tests que no versan sobre el sellado del
# acta). La carrera manual-vs-job de este módulo SÍ necesita la
# implementación REAL (para ejercitar `mark_acta_firmada`/el UPDATE en sí) --
# se captura la función ORIGINAL a nivel de import (antes de que el autouse
# la reemplace en cada test) para poder restaurarla puntualmente.
_REAL_SELLAR_ACTA_POST_COMMIT = TransferenciaService._sellar_acta_post_commit


def _fake_signed() -> SignedIndice:
    return SignedIndice(
        firma_id=uuid.uuid4(),
        minio_key_firmado="file-id/acta-transferencia-reconciliada.xml",
        nivel_conformidad="xades_b_no_acreditado",
        xades_level="XAdES-T",
        tipo_firma="sello_sistema",
        acreditado=False,
        certificado_fingerprint="itest:fingerprint",
    )


async def _seed_acta_pendiente_firma(conn: asyncpg.Connection, slug: str, name: str):
    """Recibe una transferencia REAL (sin mocks de BD) dejando su acta
    'pendiente_firma' -- el sellado post-commit se no-opea (ver docstring del
    módulo: no hay signature-service real en este tier) para que la fila
    `transferencia_acta_firma` quede tal cual la deja `recibir()`."""
    actor = Actor(id=uuid.uuid4(), username="itest-seed")
    created, transferencia = await _seed_transferencia_lista_para_recibir(conn, slug, name, actor)
    service = TransferenciaService(conn, slug)
    with patch.object(
        TransferenciaService, "_sellar_acta_post_commit", new=AsyncMock(return_value=None)
    ):
        await service.recibir(transferencia.id, actor)
    repo = TransferenciaRepository(conn, slug)
    acta = await repo.get_acta(transferencia.id)
    firma_row = await repo.get_acta_firma(transferencia.id)
    return transferencia.id, acta, firma_row


# ---------------------------------------------------------------------------
# (a) `insert_acta_firma_pendiente` atómica con `recibir()` -- toda acta
# recibida tiene su fila, incluso si el sellado post-commit nunca corre.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_recibir_always_creates_acta_firma_row_even_if_post_commit_sign_never_runs(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    actor = Actor(id=uuid.uuid4(), username="itest-seed")
    created, transferencia = await _seed_transferencia_lista_para_recibir(
        conn, slug, "itest acta firma pendiente siempre", actor
    )
    service = TransferenciaService(conn, slug)

    # Simula el proceso muriendo justo tras el commit de `recibir()` -- ANTES
    # de que el sellado síncrono post-commit siquiera se intente. Si
    # `insert_acta_firma_pendiente` no fuera atómica con `insert_acta`
    # (ambos dentro de la MISMA `conn.transaction()`), esta fila faltaría.
    with patch.object(
        TransferenciaService,
        "_sellar_acta_post_commit",
        new=AsyncMock(side_effect=RuntimeError("boom-post-commit (itest, proceso muerto)")),
    ):
        with pytest.raises(RuntimeError, match="boom-post-commit"):
            await service.recibir(transferencia.id, actor)

    # La recepción YA se comprometió (commit real) antes del boom -- desde
    # una SEGUNDA conexión, tanto el acta como su fila de firma-tracking
    # deben existir.
    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        acta_row = await second_conn.fetchrow(
            f'SELECT acta_fuid_xml FROM "{schema}".transferencia_acta WHERE transferencia_id = $1',
            transferencia.id,
        )
        firma_row = await second_conn.fetchrow(
            f'SELECT estado FROM "{schema}".transferencia_acta_firma WHERE transferencia_id = $1',
            transferencia.id,
        )
        transferencia_row = await second_conn.fetchrow(
            f'SELECT estado FROM "{schema}".transferencias WHERE id = $1', transferencia.id
        )
    finally:
        await second_conn.close()

    assert acta_row is not None
    assert firma_row is not None
    assert firma_row["estado"] == "pendiente_firma"
    assert transferencia_row["estado"] == "recibida"


# ---------------------------------------------------------------------------
# (b) Atomicidad efecto<->asiento de `_reconciliar_acta`: fallo DESPUÉS de
# audit.append, dentro de la MISMA transacción.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_reconciliar_acta_failure_after_audit_append_reverts_mark_firmada(
    tenant_ctx: tuple[str, asyncpg.Connection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    tid, acta, firma_row = await _seed_acta_pendiente_firma(conn, slug, "itest acta reconcile boom")
    row = {
        "transferencia_id": tid,
        "acta_fuid_xml": acta["acta_fuid_xml"],
        "indice_version": acta["indice_version"] or 0,
        "reconcile_intentos": firma_row["reconcile_intentos"],
    }

    original_append = audit.append

    async def _append_then_boom(*args: object, **kwargs: object) -> str:
        await original_append(*args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError("boom-post-audit (acta reconciliacion, b2 fault injection)")

    monkeypatch.setattr(audit, "append", _append_then_boom)

    with patch(
        "app.jobs.indice_reconciliation.signature_client.sign_indice_internal",
        new=AsyncMock(return_value=SignOutcome(signed=_fake_signed())),
    ):
        with pytest.raises(RuntimeError, match="boom-post-audit"):
            await _reconciliar_acta(conn, slug, row)

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        estado = await second_conn.fetchval(
            f'SELECT estado FROM "{schema}".transferencia_acta_firma WHERE transferencia_id = $1',
            tid,
        )
        firmada_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.acta_firmada",
        )
    finally:
        await second_conn.close()

    assert estado == "pendiente_firma"
    assert firmada_audit == 0

    ok, broken_id = await audit.verify_chain(conn, slug)
    assert ok is True
    assert broken_id is None


@pytest.mark.integration
async def test_reconciliar_acta_commits_firmada_and_audit_together(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    tid, acta, firma_row = await _seed_acta_pendiente_firma(conn, slug, "itest acta reconcile OK")
    row = {
        "transferencia_id": tid,
        "acta_fuid_xml": acta["acta_fuid_xml"],
        "indice_version": acta["indice_version"] or 0,
        "reconcile_intentos": firma_row["reconcile_intentos"],
    }
    signed = _fake_signed()

    with patch(
        "app.jobs.indice_reconciliation.signature_client.sign_indice_internal",
        new=AsyncMock(return_value=SignOutcome(signed=signed)),
    ):
        resultado = await _reconciliar_acta(conn, slug, row)

    assert resultado == "sellado"

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        firma = await second_conn.fetchrow(
            f'SELECT estado, firma_id, reconcile_intentos, reconcile_agotado '
            f'FROM "{schema}".transferencia_acta_firma WHERE transferencia_id = $1',
            tid,
        )
        audit_row = await second_conn.fetchrow(
            "SELECT actor, canal, payload FROM public.audit_log "
            "WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.acta_firmada",
        )
    finally:
        await second_conn.close()

    assert firma["estado"] == "firmado"
    assert firma["firma_id"] == signed.firma_id
    assert firma["reconcile_intentos"] == 0
    assert firma["reconcile_agotado"] is False

    assert audit_row is not None
    assert audit_row["canal"] == "system"
    from app.core.constants import SYSTEM_RECONCILER_ID

    assert audit_row["actor"] == str(SYSTEM_RECONCILER_ID)
    payload = json.loads(audit_row["payload"])
    assert payload["origen"] == "reconciliation"

    ok, broken_id = await audit.verify_chain(conn, slug)
    assert ok is True
    assert broken_id is None


# ---------------------------------------------------------------------------
# (c) Idempotencia REAL: carrera manual (`firmar_acta_retry`) vs. job
# (`_reconciliar_acta`) sobre la MISMA acta pendiente -- gemela de
# `test_manual_retry_vs_job_race_does_not_write_phantom_audit` (índice).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_manual_retry_vs_job_race_does_not_write_phantom_audit_acta(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    tid, acta, firma_row = await _seed_acta_pendiente_firma(
        conn, slug, "itest acta carrera manual-vs-job"
    )
    actor = Actor(id=uuid.uuid4(), username="itest-user")

    async def _manual_attempt() -> object:
        own_conn = await asyncpg.connect(dsn=DSN)
        try:
            await own_conn.execute(f'SET search_path = "{schema}", public')
            # Restaura la implementación REAL de `_sellar_acta_post_commit`
            # (el autouse `_no_real_http_acta_sign` la no-opea por defecto en
            # todo este tier) -- este test SÍ necesita ejercitar el UPDATE
            # real (`mark_acta_firmada`) para probar la carrera.
            with (
                patch.object(
                    TransferenciaService,
                    "_sellar_acta_post_commit",
                    new=_REAL_SELLAR_ACTA_POST_COMMIT,
                ),
                patch(
                    "app.clients.signature_client.sign_xml_outcome",
                    new=AsyncMock(return_value=SignOutcome(signed=_fake_signed())),
                ),
            ):
                svc = TransferenciaService(own_conn, slug)
                return await svc.firmar_acta_retry(tid, actor)
        finally:
            await own_conn.close()

    async def _job_attempt() -> str:
        own_conn = await asyncpg.connect(dsn=DSN)
        try:
            await own_conn.execute(f'SET search_path = "{schema}", public')
            row = {
                "transferencia_id": tid,
                "acta_fuid_xml": acta["acta_fuid_xml"],
                "indice_version": acta["indice_version"] or 0,
                "reconcile_intentos": firma_row["reconcile_intentos"],
            }
            with patch(
                "app.jobs.indice_reconciliation.signature_client.sign_indice_internal",
                new=AsyncMock(return_value=SignOutcome(signed=_fake_signed())),
            ):
                return await _reconciliar_acta(own_conn, slug, row)
        finally:
            await own_conn.close()

    manual_result, job_result = await asyncio.gather(
        _manual_attempt(), _job_attempt(), return_exceptions=True
    )

    assert job_result == "sellado"  # el job SIEMPRE reporta 'sellado' si obtuvo un signed
    if isinstance(manual_result, HTTPException):
        assert manual_result.status_code == 409
        assert manual_result.detail["error"] == "acta_ya_firmada"
    else:
        assert not isinstance(manual_result, BaseException)

    verify_conn = await asyncpg.connect(dsn=DSN)
    try:
        firmada_audit_count = await verify_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.acta_firmada",
        )
        estado = await verify_conn.fetchval(
            f'SELECT estado FROM "{schema}".transferencia_acta_firma WHERE transferencia_id = $1',
            tid,
        )
    finally:
        await verify_conn.close()

    # Exactamente UN asiento -- ninguna rama duplicó ni omitió el suyo.
    assert firmada_audit_count == 1
    assert estado == "firmado"

    ok, broken_id = await audit.verify_chain(conn, slug)
    assert ok is True
    assert broken_id is None


# ---------------------------------------------------------------------------
# (d) Acta AGOTADA permanece 'pendiente_firma' -- nunca inventa conformidad.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_reconciliar_acta_agotada_permanece_pendiente_firma(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "indice_reconciliation_max_intentos", 1)
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    tid, acta, firma_row = await _seed_acta_pendiente_firma(conn, slug, "itest acta agotada")
    row = {
        "transferencia_id": tid,
        "acta_fuid_xml": acta["acta_fuid_xml"],
        "indice_version": acta["indice_version"] or 0,
        "reconcile_intentos": firma_row["reconcile_intentos"],
    }

    with patch(
        "app.jobs.indice_reconciliation.signature_client.sign_indice_internal",
        new=AsyncMock(return_value=SignOutcome(signed=None, transient=True)),
    ):
        resultado = await _reconciliar_acta(conn, slug, row)

    assert resultado == "agotado"

    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        firma = await second_conn.fetchrow(
            f'SELECT estado, reconcile_agotado, reconcile_intentos, reconcile_proximo_at '
            f'FROM "{schema}".transferencia_acta_firma WHERE transferencia_id = $1',
            tid,
        )
        agotado_audit = await second_conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            "archive.acta_reconcile_agotado",
        )
    finally:
        await second_conn.close()

    assert firma["estado"] == "pendiente_firma"
    assert firma["reconcile_agotado"] is True
    assert firma["reconcile_intentos"] == 1
    assert firma["reconcile_proximo_at"] is None
    assert agotado_audit == 1

    from app.repositories.transferencia_repository import TransferenciaRepository

    pending_after = await TransferenciaRepository(conn, slug).list_acta_firmas_pendientes(10)
    assert tid not in {r["transferencia_id"] for r in pending_after}

    ok, broken_id = await audit.verify_chain(conn, slug)
    assert ok is True
    assert broken_id is None
