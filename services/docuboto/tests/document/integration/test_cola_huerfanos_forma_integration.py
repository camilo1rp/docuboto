# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Cola de binarios huérfanos de una importación: forma de la tabla.

Postgres real: el CHECK y el UNIQUE tienen que rechazar lo que declaran rechazar.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from document.core.database import tenant_schema

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_la_cola_de_huerfanos_rechaza_estado_invalido_y_duplicados(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    imp, fid = uuid.uuid4(), uuid.uuid4()
    await conn.execute(
        "INSERT INTO import_binario_huerfano (import_uuid, file_id, subido_desde) VALUES ($1, $2, now())",
        imp, fid)
    with pytest.raises(asyncpg.UniqueViolationError):
        await conn.execute(
            "INSERT INTO import_binario_huerfano (import_uuid, file_id, subido_desde) VALUES ($1, $2, now())",
            imp, fid)
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            "INSERT INTO import_binario_huerfano (import_uuid, file_id, subido_desde, estado) "
            "VALUES ($1, $2, now(), 'inventado')", uuid.uuid4(), uuid.uuid4())
