# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La importación no puede mover ni desbordar el contador nativo (AREA-09-14).

Contra Postgres REAL: `last_seq` es INTEGER y la reconciliación usa GREATEST; el
desbordamiento y el salto de numeración solo existen en el motor.

Mutaciones verificadas al entregar: (a) quitar la comparación del código de
entidad → el primer caso deja 999999; (b) quitar el tope de secuencia → el tercer
caso deja INT_MAX y la radicación nativa siguiente cae con «integer out of range».
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.schemas.document import DocumentCreate
from app.services.document_service import DocumentService
from app.services.import_service import ImportService, PackageValidationError
from tests.test_import import _pkg

pytestmark = pytest.mark.integration

_ANIO = datetime.now(tz=UTC).year


async def _importar(conn: asyncpg.Connection, slug: str, tracking: str) -> dict:
    with patch("app.services.import_service.storage_client.upload_bytes", new=AsyncMock()):
        return await ImportService(conn, slug).import_package(
            zip_bytes=_pkg([{"tracking": tracking}]), max_clearance=3, actor=None
        )


async def _last_seq(conn: asyncpg.Connection) -> int | None:
    return await conn.fetchval(
        "SELECT last_seq FROM tracking_sequences WHERE year = $1 AND doc_type = 'E'", _ANIO
    )


@pytest.mark.asyncio
async def test_numero_de_otra_entidad_no_toca_el_contador(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    await _importar(conn, slug, f"{_ANIO}-OTRA-E-999999")
    assert await _last_seq(conn) is None
    assert await conn.fetchval("SELECT count(*) FROM radicados") == 1  # sí se importó


@pytest.mark.asyncio
async def test_numero_propio_adelanta_el_contador_por_maximo(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    await conn.execute(
        "INSERT INTO tracking_sequences (year, doc_type, last_seq) VALUES ($1, 'E', 10)", _ANIO
    )
    await _importar(conn, slug, f"{_ANIO}-{slug.upper()}-E-000050")
    assert await _last_seq(conn) == 50


@pytest.mark.asyncio
async def test_secuencia_desbordada_es_422_y_la_radicacion_nativa_sigue_viva(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    with pytest.raises(PackageValidationError):
        await _importar(conn, slug, f"{_ANIO}-{slug.upper()}-E-2147483647")
    assert await _last_seq(conn) is None
    nativo = await DocumentService(conn, slug).create(
        DocumentCreate(doc_type="E", subject="Radicado nativo posterior"), None
    )
    assert nativo.tracking_number.endswith("-E-000001")
