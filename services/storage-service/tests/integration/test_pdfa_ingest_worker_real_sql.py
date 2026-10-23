# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Aislamiento multi-tenant REAL del worker asíncrono de validación PDF/A de
ingesta (`run_ingest_validation_background`, E10 Increment C2b follow-up,
E07) contra Postgres genuino -- un mock de `conn` NUNCA puede probar que
`SET search_path` de verdad confina las escrituras al schema correcto; solo
el motor real puede.

Corre con: `docker compose run --rm -e RUN_INTEGRATION=1 storage-service
pytest -q -m integration tests/integration`

También cubre, contra Postgres real:
  - el worker adquiere su PROPIA conexión del pool (`asyncpg.create_pool`
    genuino, no un mock) y JAMÁS lanza, incluso si la descarga de MinIO
    revienta;
  - correr el worker dos veces para el mismo `file_id` converge a un estado
    final coherente en `files.pdfa_status`;
  - la migración 013 se reaplica idempotente sobre un schema ya migrado, y
    su CHECK constraint rechaza de verdad un `pdfa_status` inválido;
  - REMEDIADO (auditoría Media, E07): `tenant_ctx` (este mismo conftest) ya
    genera slugs con guion (`itest-xxxxxxxx`) -- la forma válida y habitual
    de un tenant_slug real en TODO el resto de OrpycaMCP.
    `FileRepository` reusa ahora la MISMA regex/normalización canónica que
    `app.core.database.tenant_schema()` (usado por el propio `get_db_conn`
    de este servicio) y `PreservacionRepository` -- un slug guionado, pasado
    SIN sanear al worker (tal como llegaría de `X-Tenant-Slug`), aterriza su
    veredicto con normalidad. Ver `test_hyphenated_tenant_slug_worker_lands_verdict`
    más abajo, antes el síntoma del gap.
"""

from __future__ import annotations

import hashlib
import itertools
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
import pytest_asyncio

from app.core.database import tenant_schema
from app.services.pdfa_ingest_service import run_ingest_validation_background
from migrations import runner

from .conftest import DSN

pytestmark = pytest.mark.integration

_PDF_CONTENT = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\ncontenido pdf real (itest worker cross-tenant)\n%%EOF"
_PDF_SHA256 = hashlib.sha256(_PDF_CONTENT).hexdigest()

_second_slug_counter = itertools.count()


def _make_second_slug(node_name: str) -> str:
    raw = f"{node_name}:second:{next(_second_slug_counter)}:{uuid.uuid4()}"
    hexpart = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"itest-{hexpart}"


@pytest_asyncio.fixture
async def other_tenant(db_conn: asyncpg.Connection, request: pytest.FixtureRequest):
    """Segundo tenant desechable -- MISMA infraestructura que `tenant_ctx`
    (`tests/integration/conftest.py`), pero con su propio contador de slug:
    un test de aislamiento cruzado necesita DOS tenants simultáneos, y
    `tenant_ctx` (fixture function-scoped) solo provisiona uno."""
    slug = _make_second_slug(request.node.name)
    schema = tenant_schema(slug)
    await db_conn.execute(f'CREATE SCHEMA "{schema}"')
    try:
        for path in runner._sql_files(runner.TENANT_DIR):
            async with db_conn.transaction():
                await db_conn.execute(f'SET LOCAL search_path = "{schema}"')
                await db_conn.execute(path.read_text())
        yield slug
    finally:
        assert schema.startswith("tenant_itest_"), (
            f"guardarraíl: me niego a hacer DROP SCHEMA CASCADE de {schema!r}"
        )
        await db_conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


async def _seed_file(
    conn: asyncpg.Connection, slug: str, *, file_id: uuid.UUID, pdfa_status: str | None = None
) -> None:
    schema = tenant_schema(slug)
    await conn.execute(f'SET search_path = "{schema}"')
    await conn.execute(
        """
        INSERT INTO files (id, filename, mime_type, file_size, sha256, bucket, object_key, pdfa_status)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        file_id,
        "oficio.pdf",
        "application/pdf",
        len(_PDF_CONTENT),
        _PDF_SHA256,
        f"orpycamcp-{slug}-documents",
        f"{file_id}/oficio.pdf",
        pdfa_status,
    )
    await conn.execute("SET search_path = public")


async def _fetch_file_pdfa(conn: asyncpg.Connection, slug: str, file_id: uuid.UUID):
    schema = tenant_schema(slug)
    await conn.execute(f'SET search_path = "{schema}"')
    row = await conn.fetchrow(
        "SELECT pdfa_status, pdfa_profile, pdfa_checked_at FROM files WHERE id = $1", file_id
    )
    await conn.execute("SET search_path = public")
    return row


async def _count_pdfa_eventos(conn: asyncpg.Connection, slug: str, file_id: uuid.UUID) -> int:
    schema = tenant_schema(slug)
    await conn.execute(f'SET search_path = "{schema}"')
    count = await conn.fetchval(
        "SELECT count(*) FROM preservacion_evento WHERE tipo = 'VALIDACION_PDFA' AND object_ref = $1",
        str(file_id),
    )
    await conn.execute("SET search_path = public")
    return count


def _fake_minio() -> MagicMock:
    minio = MagicMock()

    async def _get_object(_bucket: str, _key: str):
        obj = MagicMock()
        obj.read = AsyncMock(return_value=_PDF_CONTENT)
        obj.close = MagicMock()
        return obj

    minio.get_object = AsyncMock(side_effect=_get_object)
    return minio


# ---------------------------------------------------------------------------
# Aislamiento multi-tenant CRÍTICO
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_worker_writes_only_to_the_target_tenant_schema(
    tenant_ctx: tuple[str, asyncpg.Connection],
    other_tenant: str,
) -> None:
    """Dos tenants, y ADVERSARIALMENTE el MISMO `file_id` en ambos (si el
    worker resolviera el schema mal, un `file_id` distinto podría ocultarlo
    por simple ausencia de fila) -- correr el worker para el tenant A
    JAMÁS debe tocar una sola fila/evento del tenant B."""
    slug_a, conn = tenant_ctx
    slug_b = other_tenant

    file_id = uuid.uuid4()
    await _seed_file(conn, slug_a, file_id=file_id)
    await _seed_file(conn, slug_b, file_id=file_id)

    pool = await asyncpg.create_pool(dsn=DSN, min_size=1, max_size=2)
    try:
        with patch("app.services.pdfa_ingest_service.get_minio", return_value=_fake_minio()):
            # `slug_a` CRUDO, con guion -- tal cual llegaría de X-Tenant-Slug.
            await run_ingest_validation_background(pool, slug_a, file_id)
    finally:
        await pool.close()

    row_a = await _fetch_file_pdfa(conn, slug_a, file_id)
    assert row_a["pdfa_status"] == "not_evaluated"  # stub honesto, PDFA_VALIDATOR=stub (default)
    assert row_a["pdfa_checked_at"] is not None
    assert await _count_pdfa_eventos(conn, slug_a, file_id) == 1

    row_b = await _fetch_file_pdfa(conn, slug_b, file_id)
    assert row_b["pdfa_status"] is None  # NUNCA tocado
    assert row_b["pdfa_checked_at"] is None
    assert await _count_pdfa_eventos(conn, slug_b, file_id) == 0


@pytest.mark.integration
async def test_worker_never_raises_with_real_pool_even_on_download_failure(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Conexión REAL adquirida de un `asyncpg.Pool` genuino (no un mock) --
    si `get_minio()` revienta, el worker se traga la excepción (best-effort
    post-respuesta) y no deja nada persistido a medias."""
    slug, conn = tenant_ctx
    file_id = uuid.uuid4()
    await _seed_file(conn, slug, file_id=file_id)

    pool = await asyncpg.create_pool(dsn=DSN, min_size=1, max_size=2)
    try:
        with patch(
            "app.services.pdfa_ingest_service.get_minio",
            side_effect=RuntimeError("MinIO caído (forzado, itest)"),
        ):
            # `slug` CRUDO, con guion -- no debe lanzar.
            await run_ingest_validation_background(pool, slug, file_id)
    finally:
        await pool.close()

    row = await _fetch_file_pdfa(conn, slug, file_id)
    assert row["pdfa_status"] is None  # nunca llegó a persistir nada
    assert await _count_pdfa_eventos(conn, slug, file_id) == 0


@pytest.mark.integration
async def test_worker_run_twice_same_file_id_converges_real_db(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Correr el worker dos veces para el MISMO `file_id` contra Postgres
    real: `files.pdfa_status` converge al MISMO veredicto ambas veces
    (coherente); `preservacion_evento` es append-only (dos corridas, dos
    eventos) -- comportamiento documentado, no un bug."""
    slug, conn = tenant_ctx
    file_id = uuid.uuid4()
    await _seed_file(conn, slug, file_id=file_id)

    pool = await asyncpg.create_pool(dsn=DSN, min_size=1, max_size=2)
    try:
        with patch("app.services.pdfa_ingest_service.get_minio", return_value=_fake_minio()):
            # `slug` CRUDO, con guion.
            await run_ingest_validation_background(pool, slug, file_id)
            await run_ingest_validation_background(pool, slug, file_id)
    finally:
        await pool.close()

    row = await _fetch_file_pdfa(conn, slug, file_id)
    assert row["pdfa_status"] == "not_evaluated"
    assert await _count_pdfa_eventos(conn, slug, file_id) == 2


# ---------------------------------------------------------------------------
# Remediación de auditoría Media (E07): slug de tenant guionado (válido y
# habitual en todo el resto del sistema) -- el worker aterriza el veredicto
# con normalidad, contra Postgres real.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_hyphenated_tenant_slug_worker_lands_verdict(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Antes de la remediación, `FileRepository._SLUG_RE` (`^[a-z0-9_]+$`,
    sin normalizar guiones) rechazaba este mismo slug crudo y el veredicto
    jamás aterrizaba (ver el HALLAZGO documentado en el docstring del
    módulo). `FileRepository` reusa ahora la MISMA regex/normalización
    canónica que `app.core.database.tenant_schema()` -- este test demuestra,
    contra Postgres real, que un slug guionado aterriza su veredicto igual
    que `test_worker_writes_only_to_the_target_tenant_schema`."""
    slug, conn = tenant_ctx
    assert "-" in slug, "tenant_ctx debería seguir generando slugs con guion (itest-<hex8>)"

    file_id = uuid.uuid4()
    await _seed_file(conn, slug, file_id=file_id)

    pool = await asyncpg.create_pool(dsn=DSN, min_size=1, max_size=2)
    try:
        with patch("app.services.pdfa_ingest_service.get_minio", return_value=_fake_minio()):
            # `slug` CRUDO, con guion -- tal cual llegaría de X-Tenant-Slug.
            await run_ingest_validation_background(pool, slug, file_id)
    finally:
        await pool.close()

    row = await _fetch_file_pdfa(conn, slug, file_id)
    assert row["pdfa_status"] == "not_evaluated"  # stub honesto, PDFA_VALIDATOR=stub (default)
    assert row["pdfa_checked_at"] is not None
    assert await _count_pdfa_eventos(conn, slug, file_id) == 1


# ---------------------------------------------------------------------------
# Migración 013 contra Postgres real
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_migration_013_reapplies_idempotently_on_real_postgres(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """`tenant_ctx` ya aplicó las migraciones tenant UNA vez (incluida la
    013); reaplicarla explícitamente una segunda vez sobre el MISMO schema
    no debe lanzar."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    path = runner.TENANT_DIR / "013_files_pdfa_ingest.sql"

    async with conn.transaction():
        await conn.execute(f'SET LOCAL search_path = "{schema}"')
        await conn.execute(path.read_text())  # segunda pasada


@pytest.mark.integration
async def test_migration_013_check_constraint_rejects_invalid_pdfa_status(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)

    async with conn.transaction():
        await conn.execute(f'SET LOCAL search_path = "{schema}"')
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO files (id, filename, file_size, sha256, bucket, object_key, pdfa_status)
                VALUES ($1, 'x.pdf', 10, $2, 'b', 'k', 'esto-no-es-un-estado-valido')
                """,
                uuid.uuid4(),
                "a" * 64,
            )
