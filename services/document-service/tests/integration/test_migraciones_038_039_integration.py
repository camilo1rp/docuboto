# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Migraciones 038 (cola de huérfanos) y 039 (lotes reanudables): idempotencia y forma.

Postgres real: re-aplicarlas no puede fallar ni tocar datos, y los CHECK/UNIQUE tienen
que rechazar lo que declaran rechazar.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import asyncpg
import pytest

from app.core.database import tenant_schema
from migrations import runner

pytestmark = pytest.mark.integration


def _sql(prefijo: str) -> str:
    (ruta,) = [p for p in runner._sql_files(runner.TENANT_DIR) if Path(p).name.startswith(prefijo)]
    return Path(ruta).read_text()


@pytest.mark.asyncio
async def test_038_y_039_son_idempotentes_y_conservan_los_datos(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    job = await conn.fetchval(
        "INSERT INTO batch_jobs (job_type, status, created_by, total_items) "
        "VALUES ('documents', 'completado_con_errores', $1, 1) RETURNING id", uuid.uuid4())
    imp = uuid.uuid4()
    await conn.execute(
        "INSERT INTO import_binario_huerfano (import_uuid, file_id, subido_desde) "
        "VALUES ($1, $2, now())", imp, uuid.uuid4())
    for prefijo in ("038_", "039_", "038_", "039_"):
        await conn.execute(_sql(prefijo))
    assert await conn.fetchval("SELECT status FROM batch_jobs WHERE id = $1", job) == "completado_con_errores"
    assert await conn.fetchval("SELECT count(*) FROM import_binario_huerfano") == 1


@pytest.mark.asyncio
async def test_la_cola_de_huerfanos_rechaza_estado_invalido_y_duplicados(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
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
