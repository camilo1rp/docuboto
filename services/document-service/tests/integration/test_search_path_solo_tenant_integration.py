# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Con el `search_path` = solo el tenant, lo global NO se resuelve sin calificar (ADR-002, C-7).

Contra Postgres REAL. El fallback que se cierra: antes, `SELECT ... FROM tenants` (o una tabla
de tenant que faltara) se resolvía en silencio contra `public`. Ahora es `UndefinedTableError`
(fail-closed), y lo legítimo —`public.tenants`, `public.unaccent`, la config de FTS calificada,
los índices trigram— sigue funcionando.

Mutaciones: devolver `, public` al `SET search_path` de `app/core/database.py` cae en
`test_el_path_de_la_conexion_de_la_app_es_solo_el_tenant` y en `test_lo_global_sin_calificar_ya_no_se_resuelve`;
`public.unaccent` -> `unaccent` cae en `test_unaccent_calificado_funciona_con_el_path_solo_tenant`
solo si se quita también la calificación del repositorio (lo cubre el guardián estático).
"""

from __future__ import annotations

import asyncpg
import pytest

from app.core.database import tenant_schema

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_lo_global_sin_calificar_ya_no_se_resuelve(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    with pytest.raises(asyncpg.UndefinedTableError):
        await conn.fetchval("SELECT 1 FROM tenants LIMIT 1")
    with pytest.raises(asyncpg.UndefinedTableError):
        await conn.fetchval("SELECT 1 FROM audit_log LIMIT 1")
    # Control positivo: calificado, sí existe (si no, lo de arriba no probaría nada).
    assert await conn.fetchval("SELECT count(*) >= 0 FROM public.tenants") is True
    assert await conn.fetchval("SELECT count(*) >= 0 FROM public.audit_log") is True


@pytest.mark.asyncio
async def test_unaccent_calificado_funciona_con_el_path_solo_tenant(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    # La comparación que hace la búsqueda y la deduplicación de terceros.
    assert await conn.fetchval("SELECT lower(public.unaccent('ÑANDÚ Pérez'))") == "nandu perez"
    with pytest.raises(asyncpg.UndefinedFunctionError):
        await conn.fetchval("SELECT lower(unaccent('ÑANDÚ'))")


@pytest.mark.asyncio
async def test_los_indices_trigram_de_la_003_se_crean_con_la_opclass_calificada(tenant_ctx) -> None:
    """La migración 003 corrió sobre un schema desechable con el path nuevo: si nombrara
    `gin_trgm_ops` a secas habría abortado al provisionar."""
    slug, conn = tenant_ctx
    filas = await conn.fetch(
        """
        SELECT i.relname, opc.opcname
          FROM pg_index x
          JOIN pg_class i ON i.oid = x.indexrelid
          JOIN pg_class t ON t.oid = x.indrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
          JOIN pg_opclass opc ON opc.oid = ANY (x.indclass::oid[])
         WHERE n.nspname = $1 AND i.relname LIKE 'ix_radicados_%_trgm'
        """,
        tenant_schema(slug),
    )
    assert {r["relname"] for r in filas} == {
        "ix_radicados_tracking_trgm",
        "ix_radicados_subject_trgm",
        "ix_radicados_sender_trgm",
    }
    assert {r["opcname"] for r in filas} == {"gin_trgm_ops"}


@pytest.mark.asyncio
async def test_la_busqueda_acentuada_y_sin_acento_con_el_path_solo_tenant(tenant_ctx) -> None:
    """FTS real con `'public.spanish_unaccent'::regconfig` y SIN `public` en el path."""
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    await conn.execute(
        "INSERT INTO radicados (tracking_number, doc_type, year, sequence, subject, tenant_code) "
        "VALUES ('2026-DEMO-E-000501', 'E', 2026, 501, 'Solicitud de certificación', 'DEMO')"
    )
    for q in ("certificación", "certificacion"):
        n = await conn.fetchval(
            "SELECT count(*) FROM radicados "
            "WHERE search_vector @@ websearch_to_tsquery('public.spanish_unaccent'::regconfig, $1)",
            q,
        )
        assert n == 1, q
