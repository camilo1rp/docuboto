# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""AREA-14-12: el catálogo de Postgres no dice que la co-firma `entrega` carezca de escritor.

La migración 027 documentó `rol` diciendo que `entrega` estaba «admitida por el CHECK pero
sin escritor todavía». **El hallazgo ya estaba cerrado por la 028** (que reescribe el COMMENT al
añadir `elabora`), así que no hace falta una migración nueva —una 059 habría pisado ese
texto, que menciona los tres roles—. Esta prueba fija el estado correcto: se lee
`col_description` DESPUÉS de correr TODAS las migraciones del tenant, que es lo que ve quien
inspecciona la base. Mutación: quitar la 028 deja el texto de la 027 y esta prueba cae.
"""

from __future__ import annotations

import pytest

from app.core.database import tenant_schema

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_el_comment_de_rol_reconoce_que_entrega_tiene_escritor(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    comentario = await conn.fetchval(
        """
        SELECT col_description(c.oid, a.attnum)
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
          JOIN pg_attribute a ON a.attrelid = c.oid
         WHERE n.nspname = $1
           AND c.relname = 'transferencia_acta_firma_personal'
           AND a.attname = 'rol'
        """,
        tenant_schema(slug),
    )
    assert comentario, "la columna no tiene COMMENT"
    assert "sin escritor" not in comentario
    assert all(rol in comentario for rol in ("recibe", "entrega", "elabora"))
