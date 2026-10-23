# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Anexo citado por una constancia de firma inobtenible vigente (C-5/C14, ADR-043) contra Postgres
REAL: la carrera cita <-> borrado se prueba con DOS conexiones.

Por qué no basta «el gate en la misma sentencia»: bajo READ COMMITTED,
`SELECT ... FROM files WHERE id = $1 AND NOT EXISTS (vista) FOR UPDATE` re-evalúa, tras esperar el
bloqueo, SOLO la fila de `files`; la subconsulta conserva la instantánea vieja y no ve una
constancia commiteada mientras esperaba. El patrón correcto (bloquear la fila y consultar la vista
en OTRA sentencia) es lo que prueba `test_la_cita_que_se_commitea_mientras_la_purga_espera_el_bloqueo_se_ve`.

MUTACIÓN que debe poner ese test en rojo: fusionar `lock_any_state` y `citas_vigentes` en una sola
sentencia (o consultar la vista ANTES de tomar `FOR UPDATE`) -> la purga borra el anexo con la
constancia ya commiteada.

El lado de archive cita con `SELECT 1 FROM files WHERE id = $1 AND deleted_at IS NULL FOR SHARE`
(el stub de aquí reproduce solo ese contrato con SQL: archive-service tiene su propia prueba).

NO se ejecutó al escribirse (regla del lote C: verificación única al cierre).

Corre con: `docker compose run --rm -e RUN_INTEGRATION=1 storage-service
pytest -q -m integration tests/integration/test_c14_cita_purga_real_sql.py`
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
import pytest_asyncio

from app.core.database import tenant_schema
from app.repositories.file_repository import FileRepository
from app.services.storage_service import AnexoCitadoPorConstanciaError, StorageService
from tests.integration.conftest import DDL_VISTA_CITAS_STUB, _require_dsn

pytestmark = pytest.mark.integration

_EXP = "EXP-C14"


async def _archivo(conn: asyncpg.Connection, slug: str) -> uuid.UUID:
    fid = uuid.uuid4()
    await FileRepository(conn, slug).create(
        file_id=fid,
        filename="anexo.pdf",
        mime_type="application/pdf",
        file_size=5,
        sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        bucket=f"orpycamcp-{slug}-documents",
        object_key=f"{fid}/anexo.pdf",
        uploaded_by=None,
    )
    return fid


def _minio() -> MagicMock:
    m = MagicMock()
    m.remove_object = AsyncMock()
    return m


async def _purgar(conn: asyncpg.Connection, slug: str, fid: uuid.UUID, minio: MagicMock) -> bool:
    with patch("app.services.storage_service.get_minio", return_value=minio):
        return await StorageService(conn, slug).purge_for_disposition(
            fid, _EXP, "Eliminación TRD, lote LOTE-1", actor=None
        )


@pytest_asyncio.fixture
async def ctx(tenant_ctx):
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS anexos (id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            radicado_id UUID NOT NULL, file_id UUID NOT NULL, filename TEXT NOT NULL DEFAULT 'x');
        CREATE TABLE IF NOT EXISTS email_mensaje (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), crudo_file_id UUID);
        CREATE TABLE IF NOT EXISTS borrador (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), documento_file_id UUID);
        CREATE TABLE IF NOT EXISTS postal_shipment (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), acuse_file_id UUID);
        CREATE TABLE IF NOT EXISTS expediente_aip_version (expediente_id UUID, version INTEGER, indice_file_id UUID);
        """
        + DDL_VISTA_CITAS_STUB
    )
    return slug, conn


@pytest_asyncio.fixture
async def otra_conexion(ctx):
    slug, _ = ctx
    c = await asyncpg.connect(dsn=_require_dsn())
    await c.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    try:
        yield c
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_sin_citas_la_purga_borra_control_positivo(ctx) -> None:
    slug, conn = ctx
    fid = await _archivo(conn, slug)
    minio = _minio()

    assert await _purgar(conn, slug, fid, minio) is True

    minio.remove_object.assert_awaited_once()
    assert not await conn.fetchval("SELECT 1 FROM files WHERE id = $1", fid)


@pytest.mark.asyncio
async def test_con_una_cita_vigente_la_purga_se_rechaza_nombrando_la_constancia(ctx) -> None:
    slug, conn = ctx
    fid = await _archivo(conn, slug)
    excepcion = uuid.uuid4()
    await conn.execute(
        "INSERT INTO citas_anexo_stub (file_id, excepcion_id) VALUES ($1, $2)", fid, excepcion
    )
    minio = _minio()

    with pytest.raises(AnexoCitadoPorConstanciaError) as e:
        await _purgar(conn, slug, fid, minio)

    assert [c.excepcion_id for c in e.value.citas] == [excepcion]
    minio.remove_object.assert_not_awaited()
    assert await conn.fetchval("SELECT 1 FROM files WHERE id = $1", fid)
    # El rechazo deja asiento APARTE de la tx del bloqueo (tiene que persistir).
    assert await conn.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND object_ref = $2 "
        "AND action = 'file.purga_bloqueada_constancia'",
        slug,
        str(fid),
    ) == 1


@pytest.mark.asyncio
async def test_revocada_la_cita_deja_de_bloquear(ctx) -> None:
    """La vista excluye la cita revocada (o con baja avalada); aquí se simula vaciando el stub."""
    slug, conn = ctx
    fid = await _archivo(conn, slug)
    await conn.execute("INSERT INTO citas_anexo_stub (file_id) VALUES ($1)", fid)
    await conn.execute("DELETE FROM citas_anexo_stub WHERE file_id = $1", fid)

    assert await _purgar(conn, slug, fid, _minio()) is True


@pytest.mark.asyncio
async def test_vista_ausente_bloquea_fail_closed(ctx) -> None:
    slug, conn = ctx
    fid = await _archivo(conn, slug)
    await conn.execute("DROP VIEW v_anexo_citado_vigente")

    with pytest.raises(AnexoCitadoPorConstanciaError) as e:
        await _purgar(conn, slug, fid, _minio())

    assert e.value.citas[0].ausente is True
    assert await conn.fetchval("SELECT 1 FROM files WHERE id = $1", fid)


@pytest.mark.asyncio
async def test_la_cita_que_se_commitea_mientras_la_purga_espera_el_bloqueo_se_ve(
    ctx, otra_conexion
) -> None:
    """LA prueba de §C14.2. Archive (conexión B) toma `FOR SHARE` sobre la fila del anexo ANTES de
    que arranque la purga, y declara la constancia después. La purga (conexión A) se queda esperando
    el `FOR UPDATE`; cuando B commitea la cita, A despierta y su SEGUNDA sentencia (instantánea
    nueva) tiene que VERLA: el archivo no se borra.

    Con una sola sentencia (`... AND NOT EXISTS (vista) FOR UPDATE`), A reevaluaría solo la fila de
    `files` con la instantánea vieja y borraría el anexo con la constancia ya commiteada."""
    slug, conn = ctx
    b = otra_conexion
    fid = await _archivo(conn, slug)
    minio = _minio()

    tx = b.transaction()
    await tx.start()
    # Lado de archive: cita con FOR SHARE (la fila existe y está viva).
    assert await b.fetchval(
        "SELECT 1 FROM files WHERE id = $1 AND deleted_at IS NULL FOR SHARE", fid
    )

    purga = asyncio.create_task(_purgar(conn, slug, fid, minio))
    await asyncio.sleep(0.5)  # A llega al FOR UPDATE y espera a B
    assert not purga.done(), "la purga no esperó al FOR SHARE de la cita: no hay serialización"

    await b.execute("INSERT INTO citas_anexo_stub (file_id) VALUES ($1)", fid)
    await tx.commit()

    with pytest.raises(AnexoCitadoPorConstanciaError):
        await purga
    minio.remove_object.assert_not_awaited()
    assert await conn.fetchval("SELECT 1 FROM files WHERE id = $1", fid), (
        "la purga borró un anexo con una constancia vigente ya commiteada"
    )


@pytest.mark.asyncio
async def test_si_la_purga_gana_la_constancia_ve_el_archivo_ya_borrado(ctx, otra_conexion) -> None:
    """Orden inverso: la purga toma el bloqueo primero; archive intenta citar con `FOR SHARE`,
    espera, y al despertar la fila ya no existe -> no puede citar (0 filas = 422
    `referencia_documental_invalida` en archive). No queda una cita colgando de un anexo borrado.

    Mutación: que la purga lea con `get_by_id_any_state` (sin `FOR UPDATE`) -> el FOR SHARE de B
    pasa al instante y la cita se commitea sobre un anexo que la purga borra."""
    slug, conn = ctx
    b = otra_conexion
    fid = await _archivo(conn, slug)
    minio = _minio()
    en_el_bloqueo = asyncio.Event()
    seguir = asyncio.Event()

    original = FileRepository.citas_vigentes

    async def _con_pausa(self, file_id):
        en_el_bloqueo.set()  # A ya tiene la fila bloqueada (FOR UPDATE)
        await seguir.wait()
        return await original(self, file_id)

    with patch.object(FileRepository, "citas_vigentes", _con_pausa):
        purga = asyncio.create_task(_purgar(conn, slug, fid, minio))
        await asyncio.wait_for(en_el_bloqueo.wait(), 5)

        async def _citar() -> object:
            async with b.transaction():
                return await b.fetchval(
                    "SELECT 1 FROM files WHERE id = $1 AND deleted_at IS NULL FOR SHARE", fid
                )

        cita = asyncio.create_task(_citar())
        await asyncio.sleep(0.5)
        assert not cita.done(), "archive citó sin esperar el bloqueo de la purga"
        seguir.set()
        assert await purga is True
        assert await asyncio.wait_for(cita, 5) is None  # la fila ya no existe: no se puede citar
