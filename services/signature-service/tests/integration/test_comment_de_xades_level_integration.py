# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""AREA-14-11: el catálogo de Postgres no dice que XAdES-T/LT/LTA estén «reservados».

La migración 008 comentó `firma.xades_level` con «T/LT/LTA reservados». Hoy los tres se
producen (T con TSA local o cliente RFC 3161 remoto real). La 008 ya está aplicada y no se
edita: la 022 corrige el COMMENT. Se lee `col_description` tras correr TODAS las migraciones
del tenant. Mutación: quitar la 022 deja el texto viejo y esta prueba cae.
"""

from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="requiere RUN_INTEGRATION=1"),
]


@pytest.mark.asyncio
async def test_el_comment_de_xades_level_no_dice_que_t_lt_lta_estan_reservados(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    schema = f"tenant_{slug.replace('-', '_')}"
    comentario = await conn.fetchval(
        """
        SELECT col_description(c.oid, a.attnum)
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
          JOIN pg_attribute a ON a.attrelid = c.oid
         WHERE n.nspname = $1 AND c.relname = 'firma' AND a.attname = 'xades_level'
        """,
        schema,
    )
    assert comentario, "la columna no tiene COMMENT"
    assert "reservados" not in comentario
    assert all(n in comentario for n in ("XAdES-B", "XAdES-T", "XAdES-LT", "XAdES-LTA"))
