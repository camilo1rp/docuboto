# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""B9-2 (SEG-B4) contra Postgres REAL: el nivel que rige la firma se relee bajo bloqueo.

``firma_solicitud.nivel_seguridad`` es una COPIA que se sincroniza por evento y puede
ir atrasada (DLQ, o una cadena declarada ``borrador`` sobre el UUID de un radicado, que
el consumidor no alcanza). ``nivel_vigente_for_update`` devuelve el MAYOR entre la
copia —bloqueada— y el nivel autoritativo vivo del objeto.

Mutaciones demostradas (cada una cae):
- quitar ``FOR UPDATE``                      -> cae la prueba de concurrencia;
- devolver solo la copia                     -> caen las de nivel vivo mayor;
- usar ``min`` en lugar de ``max``           -> cae la de nivel vivo mayor.
"""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest

from app.core.database import _schema as tenant_schema
from app.repositories.cadena_repository import CadenaRepository
from tests.integration.conftest import _require_dsn

pytestmark = pytest.mark.asyncio


async def _montar(tenant_ctx, *, copia: int, vivo: int, tipo: str = "radicado"):
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    await conn.execute(
        "CREATE TABLE radicados (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
        "tracking_number text, nivel_seguridad int NOT NULL)"
    )
    rid = await conn.fetchval(
        "INSERT INTO radicados (tracking_number, nivel_seguridad) VALUES ('R-1', $1) "
        "RETURNING id",
        vivo,
    )
    sol_id = await conn.fetchval(
        "INSERT INTO firma_solicitud (objeto_tipo, objeto_id, creado_por, nivel_seguridad) "
        "VALUES ($1, $2, $3, $4) RETURNING id",
        tipo,
        str(rid),
        uuid.uuid4(),
        copia,
    )
    return slug, conn, sol_id, str(rid)


async def test_copia_atrasada_gana_el_nivel_vivo_y_se_corrige_la_copia(tenant_ctx) -> None:
    """Cadena de nivel 1 sobre un radicado de nivel 2 (copia sin sincronizar)."""
    _slug, conn, sol_id, rid = await _montar(tenant_ctx, copia=1, vivo=2)
    repo = CadenaRepository(conn)
    async with conn.transaction():
        assert await repo.nivel_vigente_for_update(sol_id, rid) == (2, 1)
        await repo.actualizar_nivel_seguridad("radicado", [rid], 2)
    assert (
        await conn.fetchval("SELECT nivel_seguridad FROM firma_solicitud WHERE id = $1", sol_id)
        == 2
    )


async def test_cadena_declarada_borrador_sobre_el_uuid_de_un_radicado_tambien(tenant_ctx) -> None:
    """La resolución no mira el tipo declarado: ``borrador`` sobre un radicado de nivel 2."""
    _slug, conn, sol_id, rid = await _montar(tenant_ctx, copia=1, vivo=2, tipo="borrador")
    assert await CadenaRepository(conn).nivel_vigente_for_update(sol_id, rid) == (2, 1)


async def test_una_desclasificacion_no_sincronizada_no_baja_el_nivel(tenant_ctx) -> None:
    """``max`` es monótono: copia 3, vivo 1 (desclasificado, evento pendiente) => 3."""
    _slug, conn, sol_id, rid = await _montar(tenant_ctx, copia=3, vivo=1)
    assert await CadenaRepository(conn).nivel_vigente_for_update(sol_id, rid) == (3, 3)


async def test_solicitud_inexistente_devuelve_none(tenant_ctx) -> None:
    _slug, conn, _sol_id, rid = await _montar(tenant_ctx, copia=1, vivo=1)
    assert await CadenaRepository(conn).nivel_vigente_for_update(uuid.uuid4(), rid) is None


async def test_la_lectura_bloquea_la_fila_y_la_sincronizacion_concurrente_espera(
    tenant_ctx,
) -> None:
    """A lee el nivel vigente y NO confirma; B (el consumidor de ``reclassified``)
    intenta ``actualizar_nivel_seguridad`` y debe BLOQUEARSE hasta que A confirme."""
    slug, conn, sol_id, rid = await _montar(tenant_ctx, copia=1, vivo=1)
    otra = await asyncpg.connect(dsn=_require_dsn().replace("postgresql+asyncpg://", "postgresql://"))
    try:
        await otra.execute(f'SET search_path TO "{tenant_schema(slug)}"')
        tx = conn.transaction()
        await tx.start()
        try:
            await CadenaRepository(conn).nivel_vigente_for_update(sol_id, rid)
            tarea = asyncio.ensure_future(
                CadenaRepository(otra).actualizar_nivel_seguridad("radicado", [rid], 3)
            )
            hecho, _ = await asyncio.wait({tarea}, timeout=1.0)
            assert not hecho, "la sincronización no esperó al bloqueo de la lectura"
        finally:
            await tx.commit()
        assert await asyncio.wait_for(tarea, timeout=10) == 1
    finally:
        await otra.close()
