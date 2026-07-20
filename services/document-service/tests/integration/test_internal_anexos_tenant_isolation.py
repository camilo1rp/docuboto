# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Aislamiento físico por tenant (Postgres real) del inventario TOTAL de
anexos (E10 debt, cableado del AIP al cierre del expediente):
`DocumentRepository.list_anexos_for_radicados_internal`
(`app/repositories/document_repository.py`).

La query es `WHERE radicado_id = ANY($1)` SIN JOIN a `radicados` ni filtro
de tenant explícito — el aislamiento depende ENTERAMENTE de que la conexión
ya tenga el `search_path` fijado al schema correcto (lo hace `get_tenant_conn`
en producción). Este tier prueba esa garantía contra DOS schemas físicos
reales: un `radicado_id`/anexo creado en el tenant A nunca debe aparecer en
la respuesta cuando se consulta con el `search_path` del tenant B, aunque se
pida ese `radicado_id` explícitamente en el batch.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.repositories.document_repository import DocumentRepository


async def _seed_radicado_con_anexo(
    conn: asyncpg.Connection, schema: str, *, tracking_number: str
) -> tuple[uuid.UUID, uuid.UUID, str]:
    """Inserta un radicado + un anexo digital en `schema` vía SQL crudo
    (sin pasar por `DocumentService`, fuera del alcance de este tier — solo
    interesa el aislamiento de la LECTURA). Devuelve
    ``(radicado_id, file_id, filename)``."""
    radicado_row = await conn.fetchrow(
        f"""
        INSERT INTO "{schema}".radicados
            (tracking_number, doc_type, year, sequence, subject, tenant_code)
        VALUES ($1, 'E', 2026, 1, 'itest aislamiento AIP', 'ITEST')
        RETURNING id
        """,
        tracking_number,
    )
    radicado_id = radicado_row["id"]
    file_id = uuid.uuid4()
    filename = "oficio-itest.pdf"
    await conn.execute(
        f"""
        INSERT INTO "{schema}".anexos (radicado_id, file_id, filename, file_size, mime_type)
        VALUES ($1, $2, $3, 1024, 'application/pdf')
        """,
        radicado_id,
        file_id,
        filename,
    )
    return radicado_id, file_id, filename


@pytest.mark.integration
async def test_radicado_id_from_other_tenant_never_appears_in_result(
    two_tenant_ctx: tuple[str, str, asyncpg.Connection],
) -> None:
    slug_a, slug_b, conn = two_tenant_ctx
    schema_a = tenant_schema(slug_a)
    schema_b = tenant_schema(slug_b)

    radicado_a, file_a, filename_a = await _seed_radicado_con_anexo(
        conn, schema_a, tracking_number="2026-ITEST-E-000001"
    )
    radicado_b, file_b, _filename_b = await _seed_radicado_con_anexo(
        conn, schema_b, tracking_number="2026-ITEST-E-000001"  # mismo tracking_number, otro schema
    )

    # Consulta con el search_path del tenant A, pidiendo AMBOS radicado_id
    # (incluido el del tenant B) — mismo batch que enviaría archive-service
    # si (por error/ataque) mezclara IDs de tenants distintos.
    await conn.execute(f'SET search_path = "{schema_a}", public')
    repo_a = DocumentRepository(conn, slug_a)
    result_a = await repo_a.list_anexos_for_radicados_internal([radicado_a, radicado_b])

    assert str(radicado_a) in result_a or radicado_a in result_a
    assert len(result_a) == 1
    only_key = next(iter(result_a))
    assert only_key == radicado_a
    assert result_a[radicado_a][0].file_id == file_a
    assert result_a[radicado_a][0].filename == filename_a
    # El radicado del tenant B, aunque pedido explícitamente, NO aparece.
    assert radicado_b not in result_a

    # Simétrico: search_path del tenant B ve SOLO su propio radicado.
    await conn.execute(f'SET search_path = "{schema_b}", public')
    repo_b = DocumentRepository(conn, slug_b)
    result_b = await repo_b.list_anexos_for_radicados_internal([radicado_a, radicado_b])

    assert len(result_b) == 1
    assert radicado_a not in result_b
    assert result_b[radicado_b][0].file_id == file_b

    await conn.execute("SET search_path = public")


@pytest.mark.integration
async def test_radicado_without_anexos_is_absent_key_across_tenants(
    two_tenant_ctx: tuple[str, str, asyncpg.Connection],
) -> None:
    """Un `radicado_id` que SÍ existe en el tenant A pero sin ningún anexo
    digital -> ausente como clave (lista vacía implícita), NUNCA un error —
    mismo criterio para un `radicado_id` que simplemente no existe en el
    schema consultado (caso del tenant cruzado)."""
    slug_a, slug_b, conn = two_tenant_ctx
    schema_a = tenant_schema(slug_a)

    radicado_sin_anexos = await conn.fetchrow(
        f"""
        INSERT INTO "{schema_a}".radicados
            (tracking_number, doc_type, year, sequence, subject, tenant_code)
        VALUES ('2026-ITEST-E-000002', 'E', 2026, 2, 'itest sin anexos', 'ITEST')
        RETURNING id
        """
    )

    await conn.execute(f'SET search_path = "{schema_a}", public')
    repo_a = DocumentRepository(conn, slug_a)
    result = await repo_a.list_anexos_for_radicados_internal([radicado_sin_anexos["id"]])

    assert result == {}
    await conn.execute("SET search_path = public")
