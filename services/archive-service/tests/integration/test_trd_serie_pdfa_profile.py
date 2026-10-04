# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Perfil PDF/A configurable por serie/TRD (E10 debt, follow-up de
Increment C2b, RF-FIR-15) contra Postgres REAL.

Complementa `tests/test_trd_series_pdfa_profile.py` (Pydantic/HTTP, mockeado)
y `tests/test_aip_wiring_service_pdfa_profile.py` (`_resolve_aip_pdfa_profile`,
mockeado): aquí se prueba lo que un mock NO puede demostrar --

  (a) la persistencia REAL de `trd_series.pdfa_profile` a través de
      `ArchiveService.create_trd_serie`/`update_trd_serie`/
      `ArchiveRepository.get_trd_serie_by_id`;
  (b) el CHECK de la migración 035 (última línea fail-closed, defensa en
      profundidad tras la validación Pydantic) rechazando un valor fuera de
      whitelist inyectado por SQL crudo (bypass deliberado de la capa
      Pydantic, mismo criterio que `test_worm_renovacion_ct_filter.py`
      prueba el filtro CT "en la consulta SQL, no en el código Python");
  (c) la idempotencia REAL de la migración 035 (re-ejecución sobre un
      schema ya migrado por `tenant_ctx`);
  (d) el cableado end-to-end de `AipWiringService._resolve_aip_pdfa_profile`
      leyendo una fila `trd_series` REAL (no un `TrdSerie` construido a
      mano) y entregando el perfil resuelto a `storage_client.
      empaquetar_aip_internal` (mockeado, mismo compromiso que el resto de
      este directorio: sin tráfico HTTP real a storage-service).

Gating: `RUN_INTEGRATION=1` + Postgres real (ver CLAUDE.md de este servicio
y el docstring de `tests/integration/conftest.py`).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from app.clients.storage_client import AipPackagingOutcome
from app.core.database import tenant_schema
from app.schemas.archive import ExpedienteCreate, RadicadoLink, TrdSerieCreate, TrdSerieUpdate
from app.services.aip_wiring_service import AipWiringService
from app.services.archive_service import ArchiveService
from tests.conftest import datos_radicado

from .conftest import DSN, convalidate_for_test

_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations" / "tenant"
_MIGRATION_035_PATH = _MIGRATIONS_DIR / "035_trd_series_pdfa_profile.sql"


# ---------------------------------------------------------------------------
# (a) Persistencia real: CRUD de la serie con perfil.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_create_and_read_serie_with_pdfa_profile_persists_real_postgres(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    archive = ArchiveService(conn, slug)

    created = await archive.create_trd_serie(
        TrdSerieCreate(
            code=f"PDA{uuid.uuid4().hex[:6]}",
            name="itest serie con perfil 2u",
            pdfa_profile="2u",
        ),
        actor=None,
    )
    assert created.pdfa_profile == "2u"

    # Releída con una conexión NUEVA -- confirma que quedó comprometida en BD
    # (no solo devuelta desde la fila del INSERT en la misma transacción).
    other_conn = await asyncpg.connect(dsn=DSN)
    try:
        await other_conn.execute(f'SET search_path = "{tenant_schema(slug)}", public')
        reread = await ArchiveService(other_conn, slug).get_trd_serie(created.id)
    finally:
        await other_conn.close()
    assert reread.pdfa_profile == "2u"


@pytest.mark.integration
async def test_create_serie_without_pdfa_profile_persists_null_real_postgres(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    archive = ArchiveService(conn, slug)

    created = await archive.create_trd_serie(
        TrdSerieCreate(code=f"PDN{uuid.uuid4().hex[:6]}", name="itest serie sin perfil"),
        actor=None,
    )

    assert created.pdfa_profile is None
    reread = await archive.get_trd_serie(created.id)
    assert reread.pdfa_profile is None


@pytest.mark.integration
async def test_update_serie_sets_pdfa_profile_real_postgres(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    archive = ArchiveService(conn, slug)
    created = await archive.create_trd_serie(
        TrdSerieCreate(code=f"PDU{uuid.uuid4().hex[:6]}", name="itest serie a actualizar"),
        actor=None,
    )
    assert created.pdfa_profile is None

    updated = await archive.update_trd_serie(
        created.id, TrdSerieUpdate(pdfa_profile="3b"), actor=None
    )
    assert updated.pdfa_profile == "3b"

    reread = await archive.get_trd_serie(created.id)
    assert reread.pdfa_profile == "3b"


# ---------------------------------------------------------------------------
# (b) CHECK de la migración 035 -- última línea fail-closed, BYPASS
# deliberado de la validación Pydantic (SQL crudo).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_check_constraint_rejects_invalid_pdfa_profile_raw_sql(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    async with conn.transaction():
        await conn.execute(f'SET LOCAL search_path = "{schema}", public')
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                "INSERT INTO trd_series (code, name, pdfa_profile) VALUES ($1, $2, $3)",
                f"BAD{uuid.uuid4().hex[:6]}",
                "itest serie perfil inválido (bypass Pydantic)",
                "9z",
            )


@pytest.mark.integration
async def test_check_constraint_accepts_null_and_all_valid_profiles_raw_sql(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    profiles = [None, "1a", "1b", "2a", "2b", "2u", "3a", "3b", "3u"]

    async with conn.transaction():
        await conn.execute(f'SET LOCAL search_path = "{schema}", public')
        for profile in profiles:
            code = f"OK{uuid.uuid4().hex[:6]}"
            row = await conn.fetchrow(
                "INSERT INTO trd_series (code, name, pdfa_profile) VALUES ($1, $2, $3) "
                "RETURNING pdfa_profile",
                code,
                f"itest serie perfil {profile!r}",
                profile,
            )
            assert row["pdfa_profile"] == profile


# ---------------------------------------------------------------------------
# (c) Migración 035 -- idempotencia REAL contra Postgres (gemelo de
# `test_migration_034_is_idempotent_on_real_postgres` en
# `test_worm_renovacion_ct_filter.py`).
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_migration_035_is_idempotent_on_real_postgres(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    sql = _MIGRATION_035_PATH.read_text(encoding="utf-8")

    async with conn.transaction():
        await conn.execute(f'SET LOCAL search_path = "{schema}", public')
        await conn.execute(sql)  # re-ejecución sobre el schema YA migrado por tenant_ctx

    # El cableado sigue funcionando con normalidad tras la re-ejecución:
    # columna + CHECK operativos.
    archive = ArchiveService(conn, slug)
    created = await archive.create_trd_serie(
        TrdSerieCreate(
            code=f"MIG{uuid.uuid4().hex[:6]}", name="itest post-idempotencia 035", pdfa_profile="1a"
        ),
        actor=None,
    )
    assert created.pdfa_profile == "1a"

    async with conn.transaction():
        await conn.execute(f'SET LOCAL search_path = "{schema}", public')
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                "INSERT INTO trd_series (code, name, pdfa_profile) VALUES ($1, $2, $3)",
                f"MIB{uuid.uuid4().hex[:6]}",
                "itest CHECK sigue activo post-idempotencia",
                "bad",
            )


# ---------------------------------------------------------------------------
# (d) Cableado end-to-end: `_resolve_aip_pdfa_profile` sobre una fila
# `trd_series` REAL -> `storage_client.empaquetar_aip_internal` (mockeado).
# ---------------------------------------------------------------------------


async def _seed_ready_with_serie(
    conn: asyncpg.Connection, slug: str, name: str, *, pdfa_profile: str | None
) -> tuple[uuid.UUID, uuid.UUID]:
    """Expediente `closed`, índice VIGENTE `firmado` + protegido bajo WORM
    (precondición dura de `intentar_empaquetar`), UN radicado vigente, y una
    serie TRD real con el `pdfa_profile` indicado -- gemelo de `_seed_ready`
    en `test_aip_wiring_atomicity.py`, pero exponiendo el parámetro que ese
    helper no necesitaba."""
    archive = ArchiveService(conn, slug)
    serie = await archive.create_trd_serie(
        TrdSerieCreate(
            code=f"E2E{uuid.uuid4().hex[:6]}",
            name="itest serie AIP end-to-end pdfa_profile",
            retention_years=0,
            total_retention=0,
            archivo_gestion_years=5,
            archivo_central_years=5,
            pdfa_profile=pdfa_profile,
        ),
        actor=None,
    )
    # ADR-025 (Increment 1): 'borrador' (DEFAULT nuevo) no gobierna el cierre
    # de más abajo (D5); 'migrada' es el atajo de test correcto (D6) -- no
    # relacionado con lo que este helper aísla (`pdfa_profile`).
    await convalidate_for_test(conn, serie.id)
    created = await archive.create_expediente(
        payload=ExpedienteCreate(name=name, nivel_seguridad=1, trd_serie_id=serie.id),
        opened_by=None,
    )
    radicado_id = uuid.uuid4()
    await archive.link_radicado(
        created.id,
        RadicadoLink(**datos_radicado(radicado_id)),
        user_id=None,
    )
    await archive.close_expediente(created.id)

    schema = tenant_schema(slug)
    file_id = uuid.uuid4()
    await conn.execute(
        f'UPDATE "{schema}".expediente_indice SET estado = \'firmado\', firma_id = $2, '
        f"minio_key_firmado = $3 WHERE expediente_id = $1 AND estado <> 'firmado'",
        created.id,
        uuid.uuid4(),
        f"{file_id}/indice-{created.id}.xml",
    )
    await conn.execute(
        f'UPDATE "{schema}".expediente_indice SET worm_protegido_at = now(), '
        f"worm_object_id = $2 WHERE expediente_id = $1",
        created.id,
        uuid.uuid4(),
    )
    return created.id, radicado_id


@pytest.mark.integration
async def test_aip_wiring_passes_real_serie_pdfa_profile_to_storage_client(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    expediente_id, radicado_id = await _seed_ready_with_serie(
        conn, slug, "itest AIP e2e con perfil", pdfa_profile="2u"
    )
    anexos = {str(radicado_id): [{"file_id": str(uuid.uuid4()), "filename": "oficio-e2e.pdf"}]}

    with (
        patch(
            "app.services.aip_wiring_service.document_client.fetch_anexos_internal",
            new=AsyncMock(return_value=anexos),
        ),
        patch(
            "app.services.aip_wiring_service.storage_client.empaquetar_aip_internal",
            new=AsyncMock(return_value=AipPackagingOutcome(ok=True, aip_id=uuid.uuid4())),
        ) as mock_storage,
    ):
        resultado = await AipWiringService(conn, slug).intentar_empaquetar(expediente_id)

    assert resultado == "empaquetado"
    assert mock_storage.await_args.kwargs["pdfa_profile"] == "2u"


@pytest.mark.integration
async def test_aip_wiring_passes_none_when_real_serie_has_no_pdfa_profile(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    expediente_id, radicado_id = await _seed_ready_with_serie(
        conn, slug, "itest AIP e2e sin perfil", pdfa_profile=None
    )
    anexos = {str(radicado_id): [{"file_id": str(uuid.uuid4()), "filename": "oficio-e2e.pdf"}]}

    with (
        patch(
            "app.services.aip_wiring_service.document_client.fetch_anexos_internal",
            new=AsyncMock(return_value=anexos),
        ),
        patch(
            "app.services.aip_wiring_service.storage_client.empaquetar_aip_internal",
            new=AsyncMock(return_value=AipPackagingOutcome(ok=True, aip_id=uuid.uuid4())),
        ) as mock_storage,
    ):
        resultado = await AipWiringService(conn, slug).intentar_empaquetar(expediente_id)

    assert resultado == "empaquetado"
    assert mock_storage.await_args.kwargs["pdfa_profile"] is None
