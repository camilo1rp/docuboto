# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""AREA-04-10: aislamiento multi-tenant del gate no-read-up, sobre Postgres REAL.

Los tests unitarios de 04-10 afirman el SQL contra un doble de conexión; el
integrado existente prueba el gate en UN solo tenant. Aquí hay DOS schemas
reales sobre la misma base y la misma conexión, y se comprueba lo que solo
Postgres puede decidir:

- una firma de un tenant no se lee desde el ``search_path`` del otro;
- el nivel de seguridad que decide el gate es el del objeto DEL TENANT
  QUE CONSULTA: el mismo radicado (misma identidad natural) puede ser
  reservado en uno y público en el otro, y cada uno ve lo suyo;
- un objeto reservado en A no contamina la lectura de B ni al revés.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.core.database import _schema as tenant_schema
from app.repositories.firma_repository import FirmaRepository

pytestmark = pytest.mark.asyncio


async def _poblar(conn: asyncpg.Connection, slug: str, nivel: int) -> uuid.UUID:
    """Crea las tablas dueñas del nivel en el schema del tenant y una firma
    sobre el radicado ``R-COMUN`` con el ``nivel`` dado. Devuelve el id de firma."""
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    await conn.execute(
        "CREATE TABLE radicados (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
        "tracking_number text, nivel_seguridad int NOT NULL)"
    )
    await conn.execute(
        "CREATE TABLE expedientes (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
        "code text, nivel_seguridad int NOT NULL)"
    )
    await conn.execute(
        "INSERT INTO radicados (tracking_number, nivel_seguridad) VALUES ('R-COMUN', $1)",
        nivel,
    )
    return await conn.fetchval(
        """INSERT INTO firma (objeto_tipo, objeto_id, firmante_id, payload_sha256,
                              firma_blob, provider, sello_tiempo, tipo_firma, key_version)
           VALUES ('radicado', 'R-COMUN', $1, 'a', 'b', 'nativa', now(), 'nativa', 1)
           RETURNING id""",
        uuid.uuid4(),
    )


async def _desde(conn: asyncpg.Connection, slug: str) -> FirmaRepository:
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    return FirmaRepository(conn)


async def test_el_nivel_que_decide_el_gate_es_el_del_tenant_que_consulta(two_tenant_ctx) -> None:
    """Mismo número de radicado: reservado (3) en A, público (1) en B."""
    slug_a, slug_b, conn = two_tenant_ctx
    firma_a = await _poblar(conn, slug_a, 3)
    firma_b = await _poblar(conn, slug_b, 1)

    repo_a = await _desde(conn, slug_a)
    assert await repo_a.get(firma_a, max_clearance=1) is None
    assert await repo_a.get(firma_a, max_clearance=3) is not None

    repo_b = await _desde(conn, slug_b)
    assert await repo_b.get(firma_b, max_clearance=1) is not None


async def test_una_firma_no_se_lee_desde_el_search_path_de_otro_tenant(two_tenant_ctx) -> None:
    slug_a, slug_b, conn = two_tenant_ctx
    firma_a = await _poblar(conn, slug_a, 1)
    await _poblar(conn, slug_b, 1)

    repo_b = await _desde(conn, slug_b)
    # Ni con clearance máximo ni sin gate (servicio a servicio): la fila no es de B.
    assert await repo_b.get(firma_a, max_clearance=3) is None
    assert await repo_b.get(firma_a) is None
    assert await conn.fetchval("SELECT count(*) FROM firma WHERE id = $1", firma_a) == 0

    repo_a = await _desde(conn, slug_a)
    assert await repo_a.get(firma_a, max_clearance=1) is not None


async def test_lo_reservado_en_un_tenant_no_oculta_la_firma_del_otro(two_tenant_ctx) -> None:
    """El 404 del gate sale del nivel del propio schema: B (público) sigue
    leyendo su firma con clearance 1 aunque en A el mismo número sea nivel 3."""
    slug_a, slug_b, conn = two_tenant_ctx
    await _poblar(conn, slug_a, 3)
    firma_b = await _poblar(conn, slug_b, 1)

    repo_b = await _desde(conn, slug_b)
    fila = await repo_b.get(firma_b, max_clearance=1)
    assert fila is not None
    assert fila["objeto_id"] == "R-COMUN"
    # Y al volver al schema A, la consulta por el id de B no encuentra nada.
    repo_a = await _desde(conn, slug_a)
    assert await repo_a.get(firma_b, max_clearance=3) is None
