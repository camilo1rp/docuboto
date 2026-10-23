# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Borrado seguro de binarios huérfanos de una importación fallida (AREA-09-14).

Postgres REAL: la seguridad del borrado es una comprobación de referencias dentro
de la transacción, y un doble la decidiría él mismo. storage deduplica por SHA-256:
el `file_id` que devolvió la subida de una importación puede ser un objeto que
sostiene anexos de OTROS radicados, y borrarlo dejaría esos anexos apuntando a
nada. MinIO va mockeado (lo que se prueba es la decisión, no el almacén).
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
import pytest_asyncio

from app.core.database import tenant_schema
from app.repositories.file_repository import FileRepository
from app.services.storage_service import StorageService
from tests.integration.conftest import DDL_VISTA_CITAS_STUB, _require_dsn

pytestmark = pytest.mark.integration

_DDL_AJENAS = """
CREATE TABLE IF NOT EXISTS anexos (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), radicado_id UUID NOT NULL,
    file_id UUID NOT NULL, filename TEXT NOT NULL DEFAULT 'x'
);
-- `referencias()` falla cerrado si falta una tabla de la lista: las que son de
-- document-service se crean aquí (este carril solo aplica las migraciones de storage).
CREATE TABLE IF NOT EXISTS email_mensaje (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), crudo_file_id UUID
);
CREATE TABLE IF NOT EXISTS borrador (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), documento_file_id UUID
);
CREATE TABLE IF NOT EXISTS postal_shipment (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), acuse_file_id UUID
);
CREATE TABLE IF NOT EXISTS expediente_aip_version (
    expediente_id UUID, version INTEGER, indice_file_id UUID
);
""" + DDL_VISTA_CITAS_STUB


async def _archivo(
    conn,
    slug: str,
    *,
    hace: timedelta = timedelta(0),
    sha256: str | None = None,
    uploaded_by: uuid.UUID | None = None,
) -> uuid.UUID:
    """Inserta una fila de `files`; `hace` la envejece (objeto preexistente)."""
    fid = uuid.uuid4()
    await FileRepository(conn, slug).create(
        file_id=fid,
        filename="anexo.pdf",
        mime_type="application/pdf",
        file_size=5,
        sha256=sha256 or uuid.uuid4().hex + uuid.uuid4().hex,
        bucket=f"orpycamcp-{slug}-documents",
        object_key=f"{fid}/anexo.pdf",
        uploaded_by=uploaded_by,
    )
    if hace:
        await conn.execute(
            "UPDATE files SET uploaded_at = uploaded_at - $2::interval WHERE id = $1",
            fid,
            hace,
        )
    return fid


def _minio(*, falla: bool = False) -> MagicMock:
    m = MagicMock()
    m.remove_object = AsyncMock(side_effect=RuntimeError("Object Lock") if falla else None)
    return m


async def _purgar(conn, slug, fid, minio, *, desde=None):
    desde = desde or (datetime.now(tz=UTC) - timedelta(minutes=5))
    with patch("app.services.storage_service.get_minio", return_value=minio):
        return await StorageService(conn, slug).purge_orphan(
            fid, referencia="imp-1", motivo="importación revertida", actor=None, subido_desde=desde
        )


async def _existe(conn, fid) -> bool:
    return bool(await conn.fetchval("SELECT 1 FROM files WHERE id = $1", fid))


async def _asientos(conn, slug, fid) -> int:
    return await conn.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 "
        "AND action = 'file.purgado_huerfano' AND object_ref = $2",
        slug,
        str(fid),
    )


@pytest_asyncio.fixture
async def ctx(tenant_ctx):
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    await conn.execute(_DDL_AJENAS)
    return slug, conn


@pytest.mark.asyncio
async def test_sin_referencias_se_purga_objeto_fila_y_asiento(ctx) -> None:
    slug, conn = ctx
    fid = await _archivo(conn, slug)
    minio = _minio()
    r = await _purgar(conn, slug, fid, minio)
    assert r.resultado == "purgado"
    minio.remove_object.assert_awaited_once()
    assert not await _existe(conn, fid)
    assert await _asientos(conn, slug, fid) == 1


@pytest.mark.asyncio
async def test_un_anexo_de_cualquier_radicado_lo_conserva(ctx) -> None:
    """La deduplicación hace que el objeto de una importación fallida sea el MISMO
    de un anexo de un radicado ajeno: borrarlo lo dejaría colgando."""
    slug, conn = ctx
    fid = await _archivo(conn, slug)
    await conn.execute(
        "INSERT INTO anexos (radicado_id, file_id) VALUES ($1, $2)", uuid.uuid4(), fid
    )
    minio = _minio()
    r = await _purgar(conn, slug, fid, minio)
    assert r.resultado == "referenciado"
    assert "anexos.file_id" in (r.detalle or "")
    minio.remove_object.assert_not_awaited()
    assert await _existe(conn, fid)
    assert await _asientos(conn, slug, fid) == 0


@pytest.mark.asyncio
async def test_un_objeto_bajo_worm_no_se_toca(ctx) -> None:
    slug, conn = ctx
    fid = await _archivo(conn, slug)
    await conn.execute(
        "INSERT INTO preservacion_worm_objeto (expediente_id, tipo, bucket, object_key, sha256, "
        "retain_until, retention_mode, file_id) VALUES ('E-1', 'indice', 'b', 'k', $1, "
        "now() + interval '5 years', 'COMPLIANCE', $2)",
        "a" * 64,
        fid,
    )
    minio = _minio()
    r = await _purgar(conn, slug, fid, minio)
    assert r.resultado == "protegido_worm"
    minio.remove_object.assert_not_awaited()
    assert await _existe(conn, fid)


@pytest.mark.asyncio
async def test_un_objeto_anterior_a_la_importacion_no_se_toca(ctx) -> None:
    """Una coincidencia de hash con algo que ya estaba ahí no lo creó esa importación."""
    slug, conn = ctx
    fid = await _archivo(conn, slug, hace=timedelta(days=3))
    minio = _minio()
    r = await _purgar(conn, slug, fid, minio)
    assert r.resultado == "preexistente"
    minio.remove_object.assert_not_awaited()
    assert await _existe(conn, fid)


@pytest.mark.asyncio
async def test_si_el_almacen_rechaza_el_borrado_la_fila_y_el_asiento_no_ocurren(ctx) -> None:
    slug, conn = ctx
    fid = await _archivo(conn, slug)
    r = await _purgar(conn, slug, fid, _minio(falla=True))
    assert r.resultado == "error"
    assert "Object Lock" in (r.detalle or "")
    assert await _existe(conn, fid), "sin el objeto borrado no se pierde la fila: hay que reintentar"
    assert await _asientos(conn, slug, fid) == 0


@pytest.mark.asyncio
async def test_inexistente_es_no_encontrado_y_un_reintento_converge(ctx) -> None:
    slug, conn = ctx
    fid = await _archivo(conn, slug)
    assert (await _purgar(conn, slug, fid, _minio())).resultado == "purgado"
    assert (await _purgar(conn, slug, fid, _minio())).resultado == "no_encontrado"
    assert await _asientos(conn, slug, fid) == 1, "el reintento no duplica el asiento"


@pytest.mark.asyncio
async def test_si_falla_el_asiento_se_revierte_el_borrado_de_la_fila(ctx) -> None:
    """Fila y asiento son UN acto: sin asiento, la fila sigue (y el objeto, ya
    borrado, se vuelve a borrar sin daño en el reintento)."""
    slug, conn = ctx
    fid = await _archivo(conn, slug)
    with patch("app.services.storage_service.audit.append", AsyncMock(side_effect=RuntimeError("boom"))):
        with pytest.raises(RuntimeError):
            await _purgar(conn, slug, fid, _minio())
    assert await _existe(conn, fid)


@pytest.mark.asyncio
async def test_la_lista_de_referencias_cubre_cada_columna_declarada(ctx) -> None:
    """Guardián: toda tabla/columna de `REFERENCIAS_DE_ARCHIVO` que EXISTE en el tenant
    protege de verdad (una entrada mal escrita sería una referencia que nunca se ve)."""
    from app.repositories.file_repository import REFERENCIAS_DE_ARCHIVO

    slug, conn = ctx
    repo = FileRepository(conn, slug)
    fid = uuid.uuid4()
    probadas = 0
    for tabla, columna in REFERENCIAS_DE_ARCHIVO:
        if await conn.fetchval("SELECT to_regclass($1)", tabla) is None:
            continue
        # Columna válida en la tabla real (consulta de catálogo, no de datos).
        assert await conn.fetchval(
            "SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() "
            "AND table_name = $1 AND column_name = $2",
            tabla,
            columna,
        ), f"{tabla}.{columna} no existe: la comprobación de referencias lo ignoraría"
        probadas += 1
    assert probadas >= 3
    assert await repo.referencias(fid) == []


# ── carrera dedup <-> purga, y tabla ausente (auditoría, AREA-09-14) ─────────────


def _minio_completo() -> MagicMock:
    m = MagicMock()
    m.remove_object = AsyncMock()
    m.bucket_exists = AsyncMock(return_value=True)
    m.put_object = AsyncMock()
    return m


@pytest.mark.asyncio
async def test_un_objeto_reutilizado_por_dedup_no_se_purga(ctx) -> None:
    """Otra operación recibió ESTE objeto por deduplicación y aún no commitea su
    referencia: la purga no la ve. La marca `dedup_hit_at` es lo único que lo protege."""
    slug, conn = ctx
    contenido = b"contenido compartido por dedup"
    # La dedup es SOLO del mismo autor (AREA-08-09): la premisa de la prueba es que el MISMO
    # usuario sube dos veces el mismo contenido, que sigue reutilizando el objeto.
    autor = uuid.uuid4()
    fid = await _archivo(conn, slug, sha256=hashlib.sha256(contenido).hexdigest(), uploaded_by=autor)
    minio = _minio_completo()
    with patch("app.services.storage_service.get_minio", return_value=minio):
        resp, created, _ = await StorageService(conn, slug).upload(
            "otro.pdf", "application/pdf", contenido, autor
        )
        assert (created, resp.id) == (False, fid), "premisa: la subida deduplicó"
        r = await StorageService(conn, slug).purge_orphan(
            fid,
            referencia="imp-1",
            motivo="importación revertida",
            actor=None,
            subido_desde=datetime.now(tz=UTC) - timedelta(minutes=5),
        )
    assert r.resultado == "referenciado"
    assert "dedup" in (r.detalle or "")
    minio.remove_object.assert_not_awaited()
    assert await _existe(conn, fid)
    assert await _asientos(conn, slug, fid) == 0


@pytest.mark.asyncio
async def test_carrera_purga_vs_dedup_con_dos_conexiones(ctx) -> None:
    """La purga tiene la fila bloqueada y ya pasó el control; una subida idéntica
    llega por OTRA conexión. La marca de dedup espera el bloqueo (no devuelve un
    `file_id` que la purga está a punto de borrar) y, al borrarse la fila, la subida
    deja un objeto NUEVO y existente: nadie queda con un `file_id` colgante."""
    slug, conn = ctx
    contenido = b"contenido de la carrera"
    autor = uuid.uuid4()  # la dedup es solo del mismo autor (AREA-08-09)
    fid = await _archivo(conn, slug, sha256=hashlib.sha256(contenido).hexdigest(), uploaded_by=autor)
    conn2 = await asyncpg.connect(dsn=_require_dsn())
    tomado, seguir = asyncio.Event(), asyncio.Event()
    original = FileRepository.referencias

    async def referencias_detenida(self, file_id):
        tomado.set()  # la fila ya está bloqueada y el control de dedup ya se hizo
        await seguir.wait()
        return await original(self, file_id)

    minio = _minio_completo()
    try:
        with (
            patch("app.services.storage_service.get_minio", return_value=minio),
            patch.object(FileRepository, "referencias", referencias_detenida),
        ):
            purga = asyncio.create_task(
                StorageService(conn, slug).purge_orphan(
                    fid,
                    referencia="imp-1",
                    motivo="importación revertida",
                    actor=None,
                    subido_desde=datetime.now(tz=UTC) - timedelta(minutes=5),
                )
            )
            await asyncio.wait_for(tomado.wait(), 10)
            subida = asyncio.create_task(
                StorageService(conn2, slug).upload("otro.pdf", "application/pdf", contenido, autor)
            )
            await asyncio.sleep(0.7)
            assert not subida.done(), (
                "la subida devolvió el file_id de un objeto que la purga tiene bloqueado "
                "para borrar: la marca de dedup no toma el bloqueo de fila"
            )
            seguir.set()
            r = await asyncio.wait_for(purga, 10)
            resp, created, _ = await asyncio.wait_for(subida, 10)
    finally:
        seguir.set()
        await conn2.close()

    assert r.resultado == "purgado"
    assert created is True and resp.id != fid
    assert await _existe(conn, resp.id), "el file_id que recibió la subida tiene que existir"
    assert not await _existe(conn, fid)


@pytest.mark.asyncio
async def test_si_falta_una_tabla_de_referencias_no_se_borra(ctx) -> None:
    """`anexos` ausente = no se puede saber si algo lo referencia. Fail-closed."""
    slug, conn = ctx
    await conn.execute("DROP TABLE anexos")
    fid = await _archivo(conn, slug)
    assert "anexos(ausente)" in await FileRepository(conn, slug).referencias(fid)
    minio = _minio()
    r = await _purgar(conn, slug, fid, minio)
    assert r.resultado == "referenciado"
    assert "anexos" in (r.detalle or "")
    minio.remove_object.assert_not_awaited()
    assert await _existe(conn, fid)


@pytest.mark.asyncio
async def test_area_08_09_otro_autor_no_reutiliza_el_objeto_ni_ve_el_nombre_original(ctx) -> None:
    """AREA-08-09 contra la base real: el mismo contenido subido por OTRO usuario crea su
    propia fila (created=True, filename y autor propios) y no devuelve el `file_id` ni el
    nombre original del ajeno. Ni el anónimo (sin autor) deduplica."""
    slug, conn = ctx
    contenido = b"anexo reservado de otro funcionario"
    dueno, otro = uuid.uuid4(), uuid.uuid4()
    fid = await _archivo(conn, slug, sha256=hashlib.sha256(contenido).hexdigest(), uploaded_by=dueno)
    await conn.execute("UPDATE files SET filename = 'ASUNTO-RESERVADO.pdf' WHERE id = $1", fid)
    minio = _minio_completo()
    with patch("app.services.storage_service.get_minio", return_value=minio):
        resp, created, _ = await StorageService(conn, slug).upload(
            "mio.pdf", "application/pdf", contenido, otro
        )
        anon, created_anon, _ = await StorageService(conn, slug).upload(
            "anonimo.pdf", "application/pdf", contenido, None
        )
        # control positivo: el DUEÑO sí reutiliza lo suyo
        propio, created_propio, _ = await StorageService(conn, slug).upload(
            "otra-vez.pdf", "application/pdf", contenido, dueno
        )

    assert created is True and resp.id != fid and resp.filename == "mio.pdf"
    assert created_anon is True and anon.id != fid and anon.filename == "anonimo.pdf"
    assert created_propio is False and propio.id == fid
    # la marca de dedup solo se escribió sobre la fila DEL DUEÑO
    assert await conn.fetchval("SELECT dedup_hit_at IS NOT NULL FROM files WHERE id = $1", fid)
    assert not await conn.fetchval("SELECT dedup_hit_at IS NOT NULL FROM files WHERE id = $1", resp.id)


@pytest.mark.asyncio
async def test_el_carril_crea_toda_tabla_ajena_que_la_purga_consulta(ctx) -> None:
    """Deriva de `REFERENCIAS_DE_ARCHIVO`: si alguien añade una referencia nueva (como
    `postal_shipment.acuse_file_id`) y no la crea `_DDL_AJENAS`, `referencias()` falla
    cerrado y TODA la suite de purga cae con «referenciado» sin decir por qué (6 rojos
    reales). Aquí cae UNA prueba y con el nombre de la tabla."""
    from app.repositories.file_repository import REFERENCIAS_DE_ARCHIVO

    _, conn = ctx
    faltan = [t for t, _ in REFERENCIAS_DE_ARCHIVO if await conn.fetchval("SELECT to_regclass($1)", t) is None]
    assert not faltan, f"el carril de integración no crea estas tablas de referencias: {sorted(set(faltan))}"
