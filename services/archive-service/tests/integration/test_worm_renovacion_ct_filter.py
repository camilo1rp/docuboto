# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""E10 debt — renovación de retención WORM para series de disposición
Conservación Total (CT) contra Postgres REAL: el invariante CENTRAL de este
incremento es que el filtro CT vive EN LA CONSULTA SQL de
`IndexRepository.list_worm_renovacion_pending`/
`TransferenciaRepository.list_worm_renovacion_pending`
(`s.disposition = ANY($ct_codes)`) — una serie E/S/M NUNCA debe entrar al
resultado, sin importar cuán próxima esté a vencer. Ese invariante NO se
puede demostrar con mocks (un mock de repositorio no ejecuta el `WHERE` real)
— solo un `WHERE` real contra Postgres prueba que el filtro es estructural y
no delegado (por accidente u omisión) al código Python que lo consume.

También cubre: la idempotencia REAL de las migraciones 031/032 (re-ejecución
sobre un schema ya migrado) y la atomicidad marca<->asiento de
`IndexService.renovar_worm_indice`/`TransferenciaService.renovar_worm_acta`
contra BD real (commit conjunto).

Compromiso (igual que el resto de `tests/integration/`): NO hay tráfico HTTP
real a storage-service — `storage_client.renovar_retencion_worm_internal` se
monkeypatchea a un `WormRenewalOutcome` fijo.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from app.clients.storage_client import WormRenewalOutcome
from app.core.database import tenant_schema
from app.core.disposition import CT_DISPOSITION_CODES
from app.repositories.aip_repository import AipRepository
from app.repositories.index_repository import IndexRepository
from app.repositories.transferencia_repository import TransferenciaRepository
from app.schemas.archive import ExpedienteCreate, TrdSerieCreate
from app.schemas.transferencia import TransferenciaCreate
from app.core.actor import Actor
from app.services.aip_wiring_service import AipWiringService
from app.services.archive_service import ArchiveService
from app.services.index_service import IndexService
from app.services.transferencia_service import TransferenciaService

_CT_CODES = list(CT_DISPOSITION_CODES)


async def _seed_expediente_indice_firmado(
    conn: asyncpg.Connection,
    slug: str,
    name: str,
    *,
    disposition: str = "CT",
    archivo_gestion_years: int = 15,
    archivo_central_years: int = 10,
) -> tuple[uuid.UUID, asyncpg.Record]:
    """Expediente 'closed' con una serie TRD real (disposición
    PARAMETRIZABLE) y su índice vigente promovido a 'firmado' directamente
    (sin signature-service real, mismo atajo que
    `test_indice_worm_atomicity.py::_seed_expediente_firmado`). Devuelve
    ``(expediente_id, indice_row)`` -- SIN marcar `worm_protegido_at`
    (ver `_mark_indice_protegido`)."""
    schema = tenant_schema(slug)
    archive = ArchiveService(conn, slug)
    serie = await archive.create_trd_serie(
        TrdSerieCreate(
            code=f"CTF{uuid.uuid4().hex[:6]}",
            name="itest serie renovacion CT filter",
            retention_years=0,
            total_retention=0,
            disposition=disposition,
            archivo_gestion_years=archivo_gestion_years,
            archivo_central_years=archivo_central_years,
        ),
        actor=None,
    )
    created = await archive.create_expediente(
        payload=ExpedienteCreate(name=name, nivel_seguridad=1, trd_serie_id=serie.id),
        opened_by=None,
    )
    await archive.close_expediente(created.id)

    file_id = uuid.uuid4()
    row = await conn.fetchrow(
        f"""
        UPDATE "{schema}".expediente_indice
        SET estado = 'firmado', firma_id = $2, minio_key_firmado = $3
        WHERE expediente_id = $1 AND estado <> 'firmado'
        RETURNING id, expediente_id, minio_key_firmado
        """,
        created.id,
        uuid.uuid4(),
        f"{file_id}/indice-{created.id}.xml",
    )
    assert row is not None
    return created.id, row


async def _mark_indice_protegido(
    conn: asyncpg.Connection,
    slug: str,
    indice_id: uuid.UUID,
    *,
    retain_until: datetime,
    agotado: bool = False,
) -> None:
    schema = tenant_schema(slug)
    await conn.execute(
        f'UPDATE "{schema}".expediente_indice SET worm_protegido_at = now(), '
        "worm_object_id = $2, worm_retain_until = $3, worm_renovacion_agotado = $4 "
        "WHERE id = $1",
        indice_id,
        uuid.uuid4(),
        retain_until,
        agotado,
    )


async def _seed_acta_firmada(
    conn: asyncpg.Connection,
    slug: str,
    name: str,
    *,
    disposition: str = "CT",
    archivo_gestion_years: int = 15,
    archivo_central_years: int = 10,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Recibe una transferencia REAL sobre un expediente con una serie TRD
    real (disposición PARAMETRIZABLE) y promueve su acta a 'firmado'
    directamente -- mismo atajo que
    `test_transferencia_acta_worm_atomicity.py::_seed_acta_firmada`. Devuelve
    ``(transferencia_id, expediente_id)`` -- SIN marcar `worm_protegido_at`."""
    schema = tenant_schema(slug)
    archive = ArchiveService(conn, slug)
    serie = await archive.create_trd_serie(
        TrdSerieCreate(
            code=f"CTA{uuid.uuid4().hex[:6]}",
            name="itest serie acta renovacion CT filter",
            retention_years=0,
            total_retention=0,
            disposition=disposition,
            archivo_gestion_years=archivo_gestion_years,
            archivo_central_years=archivo_central_years,
        ),
        actor=None,
    )
    created = await archive.create_expediente(
        payload=ExpedienteCreate(name=name, nivel_seguridad=1, trd_serie_id=serie.id),
        opened_by=None,
    )
    await archive.close_expediente(created.id)
    await conn.execute(
        f'UPDATE "{schema}".expediente_indice SET estado = \'firmado\', firma_id = $2 '
        "WHERE expediente_id = $1 AND estado <> 'firmado'",
        created.id,
        uuid.uuid4(),
    )

    actor = Actor(id=uuid.uuid4(), username="itest-seed-ct-filter")
    transferencia = TransferenciaService(conn, slug)
    resp = await transferencia.create(
        TransferenciaCreate(tipo="primaria", expediente_id=created.id), actor
    )
    await transferencia.enviar(resp.id, actor)
    await transferencia.recibir(resp.id, actor)  # sellado post-commit no-opeado (autouse)

    file_id = uuid.uuid4()
    row = await conn.fetchrow(
        f"""
        UPDATE "{schema}".transferencia_acta_firma
        SET estado = 'firmado', firma_id = $2, minio_key_firmado = $3,
            xades_level = 'XAdES-T', acreditado = false, firmado_at = now()
        WHERE transferencia_id = $1 AND estado <> 'firmado'
        RETURNING transferencia_id
        """,
        resp.id,
        uuid.uuid4(),
        f"{file_id}/acta-{resp.id}.xml",
    )
    assert row is not None
    return resp.id, created.id


async def _mark_acta_protegido(
    conn: asyncpg.Connection,
    slug: str,
    transferencia_id: uuid.UUID,
    *,
    retain_until: datetime,
    agotado: bool = False,
) -> None:
    schema = tenant_schema(slug)
    await conn.execute(
        f'UPDATE "{schema}".transferencia_acta_firma SET worm_protegido_at = now(), '
        "worm_object_id = $2, worm_retain_until = $3, worm_renovacion_agotado = $4 "
        "WHERE transferencia_id = $1",
        transferencia_id,
        uuid.uuid4(),
        retain_until,
        agotado,
    )


async def _seed_expediente_aip_empaquetado(
    conn: asyncpg.Connection,
    slug: str,
    name: str,
    *,
    disposition: str = "CT",
    archivo_gestion_years: int = 15,
    archivo_central_years: int = 10,
) -> uuid.UUID:
    """Expediente 'closed' con una serie TRD real (disposición
    PARAMETRIZABLE) y su AIP (`expediente_aip`, migración 033) marcado
    'empaquetado' DIRECTAMENTE vía `AipRepository` -- mismo atajo que
    `_seed_expediente_indice_firmado`/`_seed_acta_firmada`, sin invocar
    `AipWiringService.intentar_empaquetar` (que exige un índice VIGENTE
    firmado+protegido bajo WORM y document-service/storage-service reales,
    fuera del alcance de ESTE incremento: solo interesa el estado FINAL
    'empaquetado' del AIP para ejercitar la 5ª fase de RENOVACIÓN). Devuelve
    `expediente_id` -- SIN poblar `worm_retain_until` (ver
    `_mark_aip_protegido`)."""
    archive = ArchiveService(conn, slug)
    serie = await archive.create_trd_serie(
        TrdSerieCreate(
            code=f"CTP{uuid.uuid4().hex[:6]}",
            name="itest serie AIP renovacion CT filter",
            retention_years=0,
            total_retention=0,
            disposition=disposition,
            archivo_gestion_years=archivo_gestion_years,
            archivo_central_years=archivo_central_years,
        ),
        actor=None,
    )
    created = await archive.create_expediente(
        payload=ExpedienteCreate(name=name, nivel_seguridad=1, trd_serie_id=serie.id),
        opened_by=None,
    )
    await archive.close_expediente(created.id)

    aip_repo = AipRepository(conn)
    await aip_repo.get_or_create(created.id)
    affected = await aip_repo.mark_empaquetado(created.id, uuid.uuid4())
    assert affected == 1
    return created.id


async def _mark_aip_protegido(
    conn: asyncpg.Connection,
    slug: str,
    expediente_id: uuid.UUID,
    *,
    retain_until: datetime,
    agotado: bool = False,
) -> None:
    schema = tenant_schema(slug)
    await conn.execute(
        f'UPDATE "{schema}".expediente_aip SET worm_retain_until = $2, '
        "worm_renovacion_agotado = $3 WHERE expediente_id = $1",
        expediente_id,
        retain_until,
        agotado,
    )


_PRONTO_A_VENCER = datetime.now(tz=UTC) + timedelta(days=30)  # dentro de cualquier ventana razonable
_LEJOS_DE_VENCER = datetime.now(tz=UTC) + timedelta(days=3650)  # 10 años -- fuera de la ventana


# ---------------------------------------------------------------------------
# (a) Filtro CT ESTRUCTURAL del índice -- una serie E/S/M NUNCA entra al
# resultado, sin importar cuán próxima esté a vencer.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_list_worm_renovacion_pending_indice_is_fail_closed_for_non_ct(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    repo = IndexRepository(conn)

    ct_id, ct_row = await _seed_expediente_indice_firmado(conn, slug, "itest CT vence", disposition="CT")
    await _mark_indice_protegido(conn, slug, ct_row["id"], retain_until=_PRONTO_A_VENCER)

    e_id, e_row = await _seed_expediente_indice_firmado(conn, slug, "itest E vence", disposition="E")
    await _mark_indice_protegido(conn, slug, e_row["id"], retain_until=_PRONTO_A_VENCER)

    conserve_id, conserve_row = await _seed_expediente_indice_firmado(
        conn, slug, "itest conserve vence", disposition="conserve"
    )
    await _mark_indice_protegido(conn, slug, conserve_row["id"], retain_until=_PRONTO_A_VENCER)

    pending = await repo.list_worm_renovacion_pending(
        ventana_dias=400, ct_codes=_CT_CODES, limit=50
    )
    pending_expediente_ids = {r["expediente_id"] for r in pending}

    # Ambos alias de Conservación Total (AGN "CT" y el alias legado
    # "conserve", ver `app/core/disposition.py`) entran; la disposición "E"
    # (Eliminación) NUNCA, aunque su worm_retain_until esté igual de próximo.
    assert ct_id in pending_expediente_ids
    assert conserve_id in pending_expediente_ids
    assert e_id not in pending_expediente_ids


@pytest.mark.integration
async def test_list_worm_renovacion_pending_indice_respects_window_and_agotado(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    repo = IndexRepository(conn)

    proximo_id, proximo_row = await _seed_expediente_indice_firmado(conn, slug, "itest proximo")
    await _mark_indice_protegido(conn, slug, proximo_row["id"], retain_until=_PRONTO_A_VENCER)

    lejos_id, lejos_row = await _seed_expediente_indice_firmado(conn, slug, "itest lejos")
    await _mark_indice_protegido(conn, slug, lejos_row["id"], retain_until=_LEJOS_DE_VENCER)

    agotado_id, agotado_row = await _seed_expediente_indice_firmado(conn, slug, "itest agotado")
    await _mark_indice_protegido(
        conn, slug, agotado_row["id"], retain_until=_PRONTO_A_VENCER, agotado=True
    )

    no_protegido_id, _ = await _seed_expediente_indice_firmado(conn, slug, "itest no protegido")

    pending = await repo.list_worm_renovacion_pending(
        ventana_dias=400, ct_codes=_CT_CODES, limit=50
    )
    pending_expediente_ids = {r["expediente_id"] for r in pending}

    assert proximo_id in pending_expediente_ids
    assert lejos_id not in pending_expediente_ids  # fuera de la ventana de 400 días
    assert agotado_id not in pending_expediente_ids  # worm_renovacion_agotado=TRUE
    assert no_protegido_id not in pending_expediente_ids  # worm_protegido_at IS NULL


# ---------------------------------------------------------------------------
# (b) Filtro CT ESTRUCTURAL del acta -- gemelo EXACTO de (a).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_list_worm_renovacion_pending_acta_is_fail_closed_for_non_ct(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    repo = TransferenciaRepository(conn, slug)

    ct_transferencia_id, _ = await _seed_acta_firmada(conn, slug, "itest acta CT vence", disposition="CT")
    await _mark_acta_protegido(conn, slug, ct_transferencia_id, retain_until=_PRONTO_A_VENCER)

    e_transferencia_id, _ = await _seed_acta_firmada(conn, slug, "itest acta E vence", disposition="E")
    await _mark_acta_protegido(conn, slug, e_transferencia_id, retain_until=_PRONTO_A_VENCER)

    pending = await repo.list_worm_renovacion_pending(
        ventana_dias=400, ct_codes=_CT_CODES, limit=50
    )
    pending_ids = {r["transferencia_id"] for r in pending}

    assert ct_transferencia_id in pending_ids
    assert e_transferencia_id not in pending_ids


@pytest.mark.integration
async def test_list_worm_renovacion_pending_acta_respects_window_and_agotado(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    repo = TransferenciaRepository(conn, slug)

    proximo_id, _ = await _seed_acta_firmada(conn, slug, "itest acta proximo")
    await _mark_acta_protegido(conn, slug, proximo_id, retain_until=_PRONTO_A_VENCER)

    lejos_id, _ = await _seed_acta_firmada(conn, slug, "itest acta lejos")
    await _mark_acta_protegido(conn, slug, lejos_id, retain_until=_LEJOS_DE_VENCER)

    agotado_id, _ = await _seed_acta_firmada(conn, slug, "itest acta agotado")
    await _mark_acta_protegido(conn, slug, agotado_id, retain_until=_PRONTO_A_VENCER, agotado=True)

    no_protegido_id, _ = await _seed_acta_firmada(conn, slug, "itest acta no protegido")

    pending = await repo.list_worm_renovacion_pending(
        ventana_dias=400, ct_codes=_CT_CODES, limit=50
    )
    pending_ids = {r["transferencia_id"] for r in pending}

    assert proximo_id in pending_ids
    assert lejos_id not in pending_ids
    assert agotado_id not in pending_ids
    assert no_protegido_id not in pending_ids


# ---------------------------------------------------------------------------
# (b-bis) Filtro CT ESTRUCTURAL del AIP (E10 debt, fast-follow del cableado
# del AIP) -- gemelo EXACTO de (a)/(b).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_list_worm_renovacion_pending_aip_is_fail_closed_for_non_ct(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    repo = AipRepository(conn)

    ct_id = await _seed_expediente_aip_empaquetado(conn, slug, "itest AIP CT vence", disposition="CT")
    await _mark_aip_protegido(conn, slug, ct_id, retain_until=_PRONTO_A_VENCER)

    e_id = await _seed_expediente_aip_empaquetado(conn, slug, "itest AIP E vence", disposition="E")
    await _mark_aip_protegido(conn, slug, e_id, retain_until=_PRONTO_A_VENCER)

    conserve_id = await _seed_expediente_aip_empaquetado(
        conn, slug, "itest AIP conserve vence", disposition="conserve"
    )
    await _mark_aip_protegido(conn, slug, conserve_id, retain_until=_PRONTO_A_VENCER)

    pending = await repo.list_worm_renovacion_pending(ventana_dias=400, ct_codes=_CT_CODES, limit=50)
    pending_ids = {r["expediente_id"] for r in pending}

    # Ambos alias de Conservación Total (AGN "CT" y el alias legado
    # "conserve") entran; la disposición "E" (Eliminación) NUNCA, aunque su
    # worm_retain_until esté igual de próximo.
    assert ct_id in pending_ids
    assert conserve_id in pending_ids
    assert e_id not in pending_ids


@pytest.mark.integration
async def test_list_worm_renovacion_pending_aip_respects_window_and_agotado(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    repo = AipRepository(conn)

    proximo_id = await _seed_expediente_aip_empaquetado(conn, slug, "itest AIP proximo")
    await _mark_aip_protegido(conn, slug, proximo_id, retain_until=_PRONTO_A_VENCER)

    lejos_id = await _seed_expediente_aip_empaquetado(conn, slug, "itest AIP lejos")
    await _mark_aip_protegido(conn, slug, lejos_id, retain_until=_LEJOS_DE_VENCER)

    agotado_id = await _seed_expediente_aip_empaquetado(conn, slug, "itest AIP agotado")
    await _mark_aip_protegido(conn, slug, agotado_id, retain_until=_PRONTO_A_VENCER, agotado=True)

    # AIP 'pendiente' (nunca empaquetado) -- creado pero SIN marcar
    # 'empaquetado', mismo criterio que "no protegido" para índice/acta.
    no_empaquetado_expediente_id = await _seed_expediente_aip_empaquetado(
        conn, slug, "itest AIP base sin empaquetar aun"
    )
    schema = tenant_schema(slug)
    await conn.execute(
        f'UPDATE "{schema}".expediente_aip SET estado = \'pendiente\', worm_retain_until = NULL '
        "WHERE expediente_id = $1",
        no_empaquetado_expediente_id,
    )

    pending = await repo.list_worm_renovacion_pending(ventana_dias=400, ct_codes=_CT_CODES, limit=50)
    pending_ids = {r["expediente_id"] for r in pending}

    assert proximo_id in pending_ids
    assert lejos_id not in pending_ids  # fuera de la ventana de 400 días
    assert agotado_id not in pending_ids  # worm_renovacion_agotado=TRUE
    assert no_empaquetado_expediente_id not in pending_ids  # estado <> 'empaquetado'


# ---------------------------------------------------------------------------
# (c) Atomicidad marca<->asiento de `renovar_worm_indice`/`renovar_worm_acta`
# contra Postgres real -- commit conjunto.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_renovar_worm_indice_commits_mirror_and_audit_together(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    expediente_id, row = await _seed_expediente_indice_firmado(conn, slug, "itest renovar indice CT")
    await _mark_indice_protegido(conn, slug, row["id"], retain_until=_PRONTO_A_VENCER)

    nueva = datetime.now(tz=UTC) + timedelta(days=9125)  # 25 años

    with patch(
        "app.clients.storage_client.renovar_retencion_worm_internal",
        new=AsyncMock(
            return_value=WormRenewalOutcome(ok=True, retain_until=nueva, renovado=True)
        ),
    ):
        resultado = await IndexService(conn, slug).renovar_worm_indice(
            expediente_id, row["id"], intentos_previos=0
        )

    assert resultado == "renovado"

    indice_row = await conn.fetchrow(
        f'SELECT worm_retain_until, worm_renovacion_intentos FROM "{schema}".expediente_indice '
        "WHERE id = $1",
        row["id"],
    )
    audit_row = await conn.fetchrow(
        "SELECT canal FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
        slug,
        "archive.indice_worm_retencion_renovada",
    )
    assert indice_row["worm_retain_until"] == nueva
    assert indice_row["worm_renovacion_intentos"] == 0
    assert audit_row is not None
    assert audit_row["canal"] == "system"


@pytest.mark.integration
async def test_renovar_worm_acta_commits_mirror_and_audit_together(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    transferencia_id, expediente_id = await _seed_acta_firmada(conn, slug, "itest renovar acta CT")
    await _mark_acta_protegido(conn, slug, transferencia_id, retain_until=_PRONTO_A_VENCER)

    nueva = datetime.now(tz=UTC) + timedelta(days=9125)

    with patch(
        "app.clients.storage_client.renovar_retencion_worm_internal",
        new=AsyncMock(
            return_value=WormRenewalOutcome(ok=True, retain_until=nueva, renovado=True)
        ),
    ):
        resultado = await TransferenciaService(conn, slug).renovar_worm_acta(
            transferencia_id, expediente_id, intentos_previos=0
        )

    assert resultado == "renovado"

    firma_row = await conn.fetchrow(
        f'SELECT worm_retain_until, worm_renovacion_intentos FROM '
        f'"{schema}".transferencia_acta_firma WHERE transferencia_id = $1',
        transferencia_id,
    )
    audit_row = await conn.fetchrow(
        "SELECT canal FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
        slug,
        "archive.acta_worm_retencion_renovada",
    )
    assert firma_row["worm_retain_until"] == nueva
    assert firma_row["worm_renovacion_intentos"] == 0
    assert audit_row is not None
    assert audit_row["canal"] == "system"


@pytest.mark.integration
async def test_renovar_worm_aip_commits_mirror_and_audit_together(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Gemelo EXACTO de `test_renovar_worm_indice_commits_mirror_and_audit_together`
    para `AipWiringService.renovar_worm_aip` (E10 debt, fast-follow del
    cableado del AIP, migración 034) -- commit conjunto marca<->asiento
    contra Postgres real."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    expediente_id = await _seed_expediente_aip_empaquetado(conn, slug, "itest renovar AIP CT")
    await _mark_aip_protegido(conn, slug, expediente_id, retain_until=_PRONTO_A_VENCER)

    nueva = datetime.now(tz=UTC) + timedelta(days=9125)  # 25 años

    with patch(
        "app.clients.storage_client.renovar_retencion_worm_internal",
        new=AsyncMock(
            return_value=WormRenewalOutcome(ok=True, retain_until=nueva, renovado=True)
        ),
    ):
        resultado = await AipWiringService(conn, slug).renovar_worm_aip(
            expediente_id, intentos_previos=0
        )

    assert resultado == "renovado"

    aip_row = await conn.fetchrow(
        f'SELECT worm_retain_until, worm_renovacion_intentos FROM "{schema}".expediente_aip '
        "WHERE expediente_id = $1",
        expediente_id,
    )
    audit_row = await conn.fetchrow(
        "SELECT canal FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
        slug,
        "archive.aip_worm_retencion_renovada",
    )
    assert aip_row["worm_retain_until"] == nueva
    assert aip_row["worm_renovacion_intentos"] == 0
    assert audit_row is not None
    assert audit_row["canal"] == "system"


# ---------------------------------------------------------------------------
# (d) Migraciones 031/032/034 -- idempotencia REAL contra Postgres.
# ---------------------------------------------------------------------------

_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations" / "tenant"
_MIGRATION_031_PATH = _MIGRATIONS_DIR / "031_expediente_indice_worm_renovacion.sql"
_MIGRATION_032_PATH = _MIGRATIONS_DIR / "032_transferencia_acta_worm_renovacion.sql"
_MIGRATION_034_PATH = _MIGRATIONS_DIR / "034_expediente_aip_worm_renovacion.sql"


@pytest.mark.integration
async def test_migration_031_is_idempotent_on_real_postgres(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    sql = _MIGRATION_031_PATH.read_text(encoding="utf-8")

    async with conn.transaction():
        await conn.execute(f'SET LOCAL search_path = "{schema}", public')
        await conn.execute(sql)  # re-ejecución sobre el schema YA migrado por tenant_ctx

    # El cableado sigue funcionando con normalidad tras la re-ejecución.
    repo = IndexRepository(conn)
    expediente_id, row = await _seed_expediente_indice_firmado(conn, slug, "itest migracion 031")
    await _mark_indice_protegido(conn, slug, row["id"], retain_until=_PRONTO_A_VENCER)
    pending = await repo.list_worm_renovacion_pending(ventana_dias=400, ct_codes=_CT_CODES, limit=50)
    assert expediente_id in {r["expediente_id"] for r in pending}


@pytest.mark.integration
async def test_migration_032_is_idempotent_on_real_postgres(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    sql = _MIGRATION_032_PATH.read_text(encoding="utf-8")

    async with conn.transaction():
        await conn.execute(f'SET LOCAL search_path = "{schema}", public')
        await conn.execute(sql)

    repo = TransferenciaRepository(conn, slug)
    transferencia_id, _ = await _seed_acta_firmada(conn, slug, "itest migracion 032")
    await _mark_acta_protegido(conn, slug, transferencia_id, retain_until=_PRONTO_A_VENCER)
    pending = await repo.list_worm_renovacion_pending(ventana_dias=400, ct_codes=_CT_CODES, limit=50)
    assert transferencia_id in {r["transferencia_id"] for r in pending}


@pytest.mark.integration
async def test_migration_034_is_idempotent_on_real_postgres(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Gemelo de `test_migration_031_is_idempotent_on_real_postgres` para la
    migración 034 (`expediente_aip.worm_retain_until`/`worm_renovacion_*`,
    E10 debt) -- re-ejecución sobre un schema YA migrado por `tenant_ctx` no
    revienta (`ADD COLUMN IF NOT EXISTS`/`CREATE INDEX IF NOT EXISTS`) y el
    cableado del AIP sigue funcionando con normalidad después."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    sql = _MIGRATION_034_PATH.read_text(encoding="utf-8")

    async with conn.transaction():
        await conn.execute(f'SET LOCAL search_path = "{schema}", public')
        await conn.execute(sql)  # re-ejecución sobre el schema YA migrado por tenant_ctx

    repo = AipRepository(conn)
    expediente_id = await _seed_expediente_aip_empaquetado(conn, slug, "itest migracion 034")
    await _mark_aip_protegido(conn, slug, expediente_id, retain_until=_PRONTO_A_VENCER)
    pending = await repo.list_worm_renovacion_pending(ventana_dias=400, ct_codes=_CT_CODES, limit=50)
    assert expediente_id in {r["expediente_id"] for r in pending}
