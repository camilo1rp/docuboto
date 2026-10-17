# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""B9-5, migración 024: ``key_version`` ya no tiene DEFAULT y el COMMENT dice «retirado».

Un DEFAULT que significa «esquema retirado» es cómo nace una fila mal etiquetada en
silencio. Se lee el catálogo de Postgres tras correr TODAS las migraciones del tenant.
Mutación: quitar los ``DROP DEFAULT`` de la 024 deja ``column_default = 0`` y cae.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.core.database import _schema as tenant_schema

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("tabla", ["firma", "firma_challenge"])
async def test_key_version_sin_default_y_con_comment_de_retirado(tenant_ctx, tabla) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    default = await conn.fetchval(
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_schema = $1 AND table_name = $2 AND column_name = 'key_version'",
        schema,
        tabla,
    )
    assert default is None
    comentario = await conn.fetchval(
        """
        SELECT col_description(c.oid, a.attnum)
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
          JOIN pg_attribute a ON a.attrelid = c.oid
         WHERE n.nspname = $1 AND c.relname = $2 AND a.attname = 'key_version'
        """,
        schema,
        tabla,
    )
    assert comentario and "retirado" in comentario


async def test_insertar_firma_sin_key_version_ya_no_es_posible(tenant_ctx) -> None:
    """Sin DEFAULT, un INSERT que olvide el esquema revienta en vez de nacer en 0."""
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    with pytest.raises(asyncpg.NotNullViolationError):
        await conn.execute(
            """INSERT INTO firma (objeto_tipo, objeto_id, firmante_id, payload_sha256,
                                  firma_blob, provider, sello_tiempo, tipo_firma)
               VALUES ('indice', 'x', $1, 'a', 'b', 'nativa', now(), 'nativa')""",
            uuid.uuid4(),
        )
