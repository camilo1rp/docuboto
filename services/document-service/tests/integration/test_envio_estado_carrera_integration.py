# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La transición postal no pisa un estado terminal ni salta el clearance (AREA-09-11).

Contra Postgres REAL: el UPDATE condicionado por estado previo y clearance es lo
que serializa al operador (callback `devuelto`) y al despacho humano. Mutación
verificada al entregar: quitar `AND estado = $4` del WHERE → el segundo caso
devuelve fila y pisa `devuelto`.
"""

from __future__ import annotations

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.repositories.postal_repository import PostalRepository

pytestmark = pytest.mark.integration


async def _envio(conn: asyncpg.Connection, nivel: int = 1) -> asyncpg.Record:
    rid = await conn.fetchval(
        "INSERT INTO radicados (tracking_number, doc_type, year, sequence, subject, "
        "tenant_code, registered_by, nivel_seguridad, clasificacion_fundamento, clasificacion_causal) "
        "VALUES ('2026-T-S-000001', 'S', 2026, 1, 'x', 'T', gen_random_uuid(), $1, "
        "CASE WHEN $1::smallint >= 2 THEN 'Ley 1712/2014 art. 19 (prueba)' END, "
        "CASE WHEN $1::smallint >= 2 THEN 'por_clasificar' END) "
        "RETURNING id",
        nivel,
    )
    return await PostalRepository(conn).insert(
        radicado_id=rid, operador="4-72", guia="C472-AAAA", destinatario=None,
        direccion=None, max_clearance=3,
    )


@pytest.mark.asyncio
async def test_el_estado_previo_viejo_no_pisa_un_terminal(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    repo = PostalRepository(conn)
    envio = await _envio(conn)
    await repo.update_estado(
        envio["id"], "en_transito", None, estado_previo="registrado", max_clearance=3
    )
    # El callback del operador deja `devuelto`...
    assert await repo.update_estado(
        envio["id"], "devuelto", None, estado_previo="en_transito", max_clearance=None
    )
    # ...y el despacho manual, que leyó `en_transito`, llega tarde.
    assert (
        await repo.update_estado(
            envio["id"], "entregado", None, estado_previo="en_transito", max_clearance=3
        )
        is None
    )
    assert await conn.fetchval("SELECT estado FROM postal_shipment WHERE id = $1", envio["id"]) == "devuelto"


@pytest.mark.asyncio
async def test_el_update_respeta_el_clearance_en_el_where(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    repo = PostalRepository(conn)
    envio = await _envio(conn, nivel=3)
    assert (
        await repo.update_estado(
            envio["id"], "en_transito", None, estado_previo="registrado", max_clearance=1
        )
        is None
    )
    assert await conn.fetchval("SELECT estado FROM postal_shipment WHERE id = $1", envio["id"]) == "registrado"
