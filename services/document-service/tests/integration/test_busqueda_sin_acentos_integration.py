# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Buscar sin tildes tiene que encontrar lo que se escribió con tildes.

Contra Postgres REAL, porque es lo único que prueba una migración: el defecto
vivía en la definición de una columna generada y en la configuración de texto
que usa la consulta. Un mock habría dado verde con `search_vector` construido
como fuera — de hecho el test unitario solo puede comprobar qué SQL se envía,
no qué devuelve.

El caso concreto que lo destapó: «certificacion» devolvía CERO radicados y
«certificación» devolvía tres, con la pantalla diciendo «Sin resultados», que
es indistinguible de «no existe».
"""

from __future__ import annotations

import asyncpg
import pytest

from app.core.database import tenant_schema

pytestmark = pytest.mark.integration

_CONFIG = "'public.spanish_unaccent'::regconfig"


async def _seed(conn: asyncpg.Connection, subject: str, seq: int) -> None:
    await conn.execute(
        "INSERT INTO radicados (tracking_number, doc_type, year, sequence, subject, "
        "tenant_code) VALUES ($1, 'E', 2026, $2, $3, 'DEMO')",
        f"2026-DEMO-E-{seq:06d}",
        seq,
        subject,
    )


async def _cuantos(conn: asyncpg.Connection, consulta: str) -> int:
    return await conn.fetchval(
        f"SELECT count(*) FROM radicados "
        f"WHERE search_vector @@ websearch_to_tsquery({_CONFIG}, $1)",
        consulta,
    )


@pytest.mark.asyncio
async def test_sin_tilde_encuentra_lo_escrito_con_tilde(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')

    await _seed(conn, "Solicitud de certificación laboral", 1)

    assert await _cuantos(conn, "certificación") == 1
    assert await _cuantos(conn, "certificacion") == 1


@pytest.mark.asyncio
async def test_y_con_tilde_encuentra_lo_escrito_sin_ella(tenant_ctx) -> None:
    """La normalización va en las DOS direcciones: quien redacta también escribe
    sin tildes, y quien busca puede escribirlas.

    Nota de la verificación por mutación: este caso concreto sigue en verde si
    se corrige SOLO la consulta y no el índice — el texto guardado ya venía sin
    acento, así que la mitad de consulta basta para casarlo. Justamente por eso
    no sirve como prueba de la migración: quien mide si las dos mitades se
    movieron es el test de arriba.
    """
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')

    await _seed(conn, "Solicitud de certificacion de residencia", 2)

    assert await _cuantos(conn, "certificación") == 1


@pytest.mark.asyncio
async def test_no_confunde_palabras_distintas(tenant_ctx) -> None:
    """Gemelo: quitar acentos no puede convertirse en «encuentra cualquier cosa».

    El atajo perezoso sería normalizar tan agresivamente que la búsqueda
    devolviera parecidos; sobre un archivo, eso es peor que no encontrar.
    """
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')

    await _seed(conn, "Solicitud de certificación laboral", 3)

    assert await _cuantos(conn, "cesantías") == 0
    assert await _cuantos(conn, "certificado laboral") == 0


@pytest.mark.asyncio
async def test_la_columna_generada_usa_la_configuracion_sin_acentos(tenant_ctx) -> None:
    """La migración recrea la columna: sin eso, el índice viejo seguiría con
    acentos y la consulta nueva no encontraría nada indexado. Las dos mitades
    se mueven juntas o no se mueve ninguna."""
    slug, conn = tenant_ctx
    esquema = tenant_schema(slug)

    expr = await conn.fetchval(
        """
        SELECT pg_get_expr(d.adbin, d.adrelid)
          FROM pg_attrdef d
          JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
          JOIN pg_class c ON c.oid = d.adrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relname = 'radicados'
           AND a.attname = 'search_vector'
           AND n.nspname = $1
        """,
        esquema,
    )
    assert expr is not None
    assert "spanish_unaccent" in expr
