# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La firma NATIVA contra Postgres real (E06/E23).

Este fichero existe por un 500 en la cara de un firmante. `firma.tipo_firma` es
`NOT NULL` desde la migración 002, el INSERT del repositorio **nombra** la
columna, y la rama nativa no le pasaba valor — así que le pasaba `NULL`
explícito. Un NULL explícito **ignora el DEFAULT**: solo se aplica cuando la
columna se omite. Resultado: `NotNullViolationError` para cualquiera que firme
sin credencial PKI personal, que es el caso común.

**Ninguna suite lo veía porque todas doblan la conexión con `AsyncMock`**, y un
mock acepta cualquier fila. En la base de demostración no había una sola firma
`nativa`: ese camino nunca había llegado a Postgres. La lección no es del
defecto sino del método — hay invariantes que solo puede afirmar quien los
impone, y aquí quien los impone es la base.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.core.database import _schema as tenant_schema
from app.repositories.firma_repository import FirmaRepository

pytestmark = pytest.mark.asyncio


async def test_una_firma_nativa_se_inserta_sin_tipo_firma_explicito(
    tenant_ctx,
) -> None:
    """El caso exacto que reventaba: la rama nativa no pasa `tipo_firma`."""
    slug, db_conn = tenant_ctx
    schema = tenant_schema(slug)
    await db_conn.execute(f'SET search_path TO "{schema}", public')

    row = await FirmaRepository(db_conn).insert(
        {
            "objeto_tipo": "borrador",
            "objeto_id": str(uuid.uuid4()),
            "formato_solicitado": None,
            "firmante_id": uuid.uuid4(),
            "payload_sha256": "a" * 64,
            "firma_blob": "b" * 64,
            "provider": "nativa",
            # La rama nativa real pasa un sello (`sello_dt`); la columna es
            # NOT NULL igual que `tipo_firma`, y una primera versión de este
            # test le pasó None y cayó — con razón: replicar la llamada de
            # verdad es la mitad del valor de este tier.
            "sello_tiempo": datetime.now(UTC),
            # Desde B9-S7 la nativa declara su esquema HMAC (sin default silencioso).
            "key_version": 1,
        }
    )

    assert row is not None
    # Y con el valor del vocabulario VIGENTE, no con el muerto de la 002
    # (`electronica-nativa`), que ya no lee nadie.
    assert row["tipo_firma"] == "nativa"


async def test_el_default_de_columna_pertenece_al_vocabulario_vigente(
    tenant_ctx,
) -> None:
    """Migración 019.

    La 002 creó la columna con `DEFAULT 'electronica-nativa'` y la 008 quiso
    redefinirla a `'nativa'` — su `IF NOT EXISTS` la convirtió en no-op. Lo
    único de la 008 que sí llegó fue el `COMMENT`, así que la columna quedó
    **documentando un vocabulario y con el DEFAULT de otro**. Nadie lo notó
    porque el DEFAULT no se usa: el INSERT nombra siempre la columna. Se
    corrige igualmente — un default que escribe un valor que ningún código lee
    es una trampa esperando a la primera inserción que omita la columna.
    """
    slug, db_conn = tenant_ctx
    schema = tenant_schema(slug)
    default = await db_conn.fetchval(
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_schema = $1 AND table_name = 'firma' "
        "  AND column_name = 'tipo_firma'",
        schema,
    )

    assert default is not None
    assert "nativa" in default
    assert "electronica-nativa" not in default
