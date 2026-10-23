# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Las transiciones del lote de eliminación son atómicas con su asiento
(AREA-06-01-B), contra Postgres REAL.

`get_db_conn` no abre transacción: sin un `conn.transaction()` explícito en el
servicio, el `UPDATE` de publicar/avalar/anular/ejecutar se confirma solo y
`audit.append` —que abre su propia transacción— corre después. Un fallo entre
las dos sentencias dejaba el lote en su estado nuevo SIN asiento: una acción
sobre una destrucción irreversible que la traza inmutable no registra.

No sirve con dobles de la conexión: el rollback lo produce Postgres. El fallo se
inyecta DESPUÉS del `UPDATE` haciendo que `audit.append` lance (no hay fallo
natural posible en ese punto), y se comprueba que `eliminacion_lotes.estado` NO
avanzó. Mutación que debe tumbarlo: sacar `audit.append` (o el `UPDATE`) del
bloque `transaction()` en el método correspondiente de `ArchiveService`.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from orpycamcp_common import audit

from app.core.database import tenant_schema
from app.services.archive_service import ArchiveService

from .test_eliminacion_lote_estados import _publicar, _sembrar_lote

pytestmark = pytest.mark.integration

AYER = date.today() - timedelta(days=1)
MANANA = date.today() + timedelta(days=30)
AVALADOR = uuid.uuid4()


class _Boom(RuntimeError):
    pass


def _audit_que_falla():
    return patch.object(audit, "append", AsyncMock(side_effect=_Boom("el asiento falló")))


async def _estado(conn: asyncpg.Connection, schema: str, lote_id: uuid.UUID) -> str:
    return await conn.fetchval(
        f'SELECT estado FROM "{tenant_schema(schema)}".eliminacion_lotes WHERE id = $1', lote_id
    )


async def _asientos(conn: asyncpg.Connection, slug: str, accion: str) -> int:
    return await conn.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
        slug,
        accion,
    )


async def _avalar_por_sql(conn: asyncpg.Connection, lote_id: uuid.UUID) -> None:
    await conn.execute(
        "UPDATE eliminacion_lotes SET estado='avalado', acta_comite='Acta 07', "
        "fecha_aval=CURRENT_DATE, acto_administrativo='Res. 512', avalado_por=$2, "
        "avalado_at=now() WHERE id=$1",
        lote_id,
        AVALADOR,
    )


# ── publicar ─────────────────────────────────────────────────────────────────


async def test_publicar_con_el_asiento_roto_NO_deja_el_lote_publicado(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    lote_id, _ = await _sembrar_lote(conn, slug)
    servicio = ArchiveService(conn, slug)

    with _audit_que_falla(), pytest.raises(_Boom):
        await servicio.publicar_lote_eliminacion(
            lote_id, publicado_hasta=MANANA, actor=uuid.uuid4()
        )

    assert await _estado(conn, slug, lote_id) == "borrador"


async def test_publicar_sin_fallo_avanza_y_deja_su_asiento(tenant_ctx) -> None:
    """Control positivo: sin él, un servicio que nunca avanzara pasaba el test
    de arriba."""
    slug, conn = tenant_ctx
    lote_id, _ = await _sembrar_lote(conn, slug)

    await ArchiveService(conn, slug).publicar_lote_eliminacion(
        lote_id, publicado_hasta=MANANA, actor=uuid.uuid4()
    )

    assert await _estado(conn, slug, lote_id) == "publicado"
    assert await _asientos(conn, slug, "archive.eliminacion_lote_publicado") == 1


# ── avalar ───────────────────────────────────────────────────────────────────


async def test_avalar_con_el_asiento_roto_NO_deja_el_lote_avalado(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    lote_id, _ = await _sembrar_lote(conn, slug)
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    await _publicar(conn, lote_id, AYER)

    with _audit_que_falla(), pytest.raises(_Boom):
        await ArchiveService(conn, slug).avalar_lote_eliminacion(
            lote_id,
            acta_comite="Acta 07",
            fecha_aval=date.today(),
            acto_administrativo="Res. 512 de 2026",
            actor=AVALADOR,
        )

    assert await _estado(conn, slug, lote_id) == "publicado"


async def test_avalar_sin_fallo_avanza_y_deja_su_asiento(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    lote_id, _ = await _sembrar_lote(conn, slug)
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    await _publicar(conn, lote_id, AYER)

    await ArchiveService(conn, slug).avalar_lote_eliminacion(
        lote_id,
        acta_comite="Acta 07",
        fecha_aval=date.today(),
        acto_administrativo="Res. 512 de 2026",
        actor=AVALADOR,
    )

    assert await _estado(conn, slug, lote_id) == "avalado"
    assert await _asientos(conn, slug, "archive.eliminacion_lote_avalado") == 1


# ── anular ───────────────────────────────────────────────────────────────────


async def test_anular_con_el_asiento_roto_NO_deja_el_lote_anulado(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    lote_id, _ = await _sembrar_lote(conn, slug)

    with _audit_que_falla(), pytest.raises(_Boom):
        await ArchiveService(conn, slug).anular_lote_eliminacion(
            lote_id, motivo="Se retira por error material", actor=uuid.uuid4()
        )

    assert await _estado(conn, slug, lote_id) == "borrador"


# ── ejecutar (marcar_lote_ejecutado + asiento) ───────────────────────────────


async def _sembrar_lote_avalado_con_acta_protegida(
    conn: asyncpg.Connection, slug: str
) -> uuid.UUID:
    """Un lote avalado SIN expedientes (la destrucción no es lo que se mide aquí)
    con su acta firmada y protegida bajo WORM, que son las precondiciones."""
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    lote_id = await conn.fetchval(
        "INSERT INTO eliminacion_lotes (codigo, creado_por) "
        "VALUES ($1, gen_random_uuid()) RETURNING id",
        f"LOTE-AT-{uuid.uuid4().hex[:6]}",
    )
    await _publicar(conn, lote_id, AYER)
    await _avalar_por_sql(conn, lote_id)
    await conn.execute(
        "INSERT INTO eliminacion_acta (lote_id, acta_xml, acta_sha256, metodo_borrado, "
        "generado_por) VALUES ($1, '<acta/>', $2, 'Borrado seguro y picado', gen_random_uuid())",
        lote_id,
        "a" * 64,
    )
    await conn.execute(
        "INSERT INTO eliminacion_acta_firma (lote_id, estado, firma_id, minio_key_firmado, "
        "firmado_at, worm_protegido_at) "
        "VALUES ($1, 'firmado', gen_random_uuid(), 'k/acta.xml', now(), now())",
        lote_id,
    )
    return lote_id


async def test_ejecutar_con_el_asiento_roto_NO_deja_el_lote_ejecutado(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    lote_id = await _sembrar_lote_avalado_con_acta_protegida(conn, slug)

    with _audit_que_falla(), pytest.raises(_Boom):
        await ArchiveService(conn, slug).ejecutar_lote_eliminacion(lote_id, actor=uuid.uuid4())

    assert await _estado(conn, slug, lote_id) == "avalado"


async def test_ejecutar_sin_fallo_marca_ejecutado_y_deja_su_asiento(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    lote_id = await _sembrar_lote_avalado_con_acta_protegida(conn, slug)

    respuesta = await ArchiveService(conn, slug).ejecutar_lote_eliminacion(
        lote_id, actor=uuid.uuid4()
    )

    assert respuesta.estado == "ejecutado"
    assert await _estado(conn, slug, lote_id) == "ejecutado"
    assert await _asientos(conn, slug, "archive.eliminacion_lote_ejecutado") == 1


async def test_el_avalador_no_ejecuta_ni_toca_el_lote(tenant_ctx) -> None:
    """La separación de funciones contra Postgres real: el mismo UUID que avaló
    recibe 409 y el lote sigue `avalado`."""
    from fastapi import HTTPException

    slug, conn = tenant_ctx
    lote_id = await _sembrar_lote_avalado_con_acta_protegida(conn, slug)

    with pytest.raises(HTTPException) as exc:
        await ArchiveService(conn, slug).ejecutar_lote_eliminacion(lote_id, actor=AVALADOR)

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "separacion_de_funciones"
    assert await _estado(conn, slug, lote_id) == "avalado"
