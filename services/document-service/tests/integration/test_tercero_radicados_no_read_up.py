# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`/terceros/{id}/radicados` respeta el no-read-up (AREA-11-X1), con Postgres real.

El hallazgo: el listado devolvía el ASUNTO de radicados RESERVADOS a cualquier
`PERM_RADI` de nivel 1. El caso fino es la ficha FUSIONADA: la rama
`OR ra.tercero_id IN (fichas absorbidas)` del `WHERE` hace que, sin paréntesis,
`a OR b AND nivel <= $3` deje la rama directa fuera del gate. Por eso se siembran
los tres vínculos (directo nivel 1, directo nivel 3, absorbido nivel 3).
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.core.database import tenant_schema

pytestmark = pytest.mark.integration


async def _tercero(conn: asyncpg.Connection, nombre: str) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO tercero (nombre, tipo) VALUES ($1, 'ciudadano') RETURNING id", nombre
    )


async def _radicar_con(
    conn: asyncpg.Connection, slug: str, tercero_id: uuid.UUID, asunto: str, nivel: int
) -> None:
    from app.schemas.document import DocumentCreate
    from app.services.document_service import DocumentService

    doc = await DocumentService(conn, slug).create(
        DocumentCreate(
            doc_type="E",
            subject=asunto,
            sender_name="quien sea",
            actores=[{"tercero_id": tercero_id, "rol": "remitente", "principal": True}],
        ),
        registered_by=None,
    )
    await conn.execute("UPDATE radicados SET nivel_seguridad = $2 WHERE id = $1", doc.id, nivel)


async def _sembrar(tenant_ctx: tuple[str, asyncpg.Connection]):
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    from app.services.tercero_service import TerceroService

    t = await _tercero(conn, "Ana Ríos")
    absorbida = await _tercero(conn, "ANA RIOS")
    await _radicar_con(conn, slug, t, "publico directo", 1)
    await _radicar_con(conn, slug, t, "reservado directo", 3)
    await _radicar_con(conn, slug, absorbida, "reservado de la absorbida", 3)
    await _radicar_con(conn, slug, absorbida, "publico de la absorbida", 1)
    servicio = TerceroService(conn, slug)
    await servicio.fusionar(t, absorbida, actor=None)
    return servicio, t


@pytest.mark.asyncio
async def test_clearance_1_solo_ve_los_de_nivel_1_tambien_por_la_ficha_fusionada(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    servicio, t = await _sembrar(tenant_ctx)
    filas = await servicio.radicados_de(t, max_clearance=1)
    assert {r["subject"] for r in filas} == {"publico directo", "publico de la absorbida"}
    assert all(r["nivel_seguridad"] == 1 for r in filas)


@pytest.mark.asyncio
async def test_clearance_3_ve_todos_control_positivo(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    servicio, t = await _sembrar(tenant_ctx)
    filas = await servicio.radicados_de(t, max_clearance=3)
    assert len(filas) == 4
    assert {r["nivel_seguridad"] for r in filas} == {1, 3}
