# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`list_radicado_metadata_internal` devuelve el ORIGEN y el FORMATO (E15 R1).

Por qué contra Postgres real y no con un mock de `conn.fetch`: la corrección es
un `LEFT JOIN LATERAL` a `anexos` ordenado por `created_at` — un mock devuelve
lo que se le diga con cualquier FROM, cualquier JOIN y cualquier ORDER BY, así
que pasaría en verde con la consulta anterior y con una consulta rota. Lo que
hay que probar es el CABLEADO: que el primer anexo es el que manda, que un
radicado sin anexos no inventa formato, y que la dependencia productora que sale
es la de ORIGEN y no la de destino (las dos columnas existen, se parecen, y
tomar la equivocada es un error que ninguna prueba de forma detecta).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.repositories.document_repository import DocumentRepository


async def _seed_radicado(
    conn: asyncpg.Connection,
    schema: str,
    *,
    tracking_number: str,
    origin_dept: str | None = "Oficina de Contratacion",
    origin_dept_code: int | None = 120,
    dest_dept: str | None = "Archivo Central",
    dest_dept_code: int | None = 900,
    nivel_seguridad: int = 1,
) -> uuid.UUID:
    row = await conn.fetchrow(
        f"""
        INSERT INTO "{schema}".radicados
            (tracking_number, doc_type, year, sequence, subject, tenant_code,
             origin_dept, origin_dept_code, dest_dept, dest_dept_code, nivel_seguridad,
             clasificacion_fundamento)
        VALUES ($1, 'E', 2026, 1, 'itest origen', 'ITEST', $2, $3, $4, $5, $6,
                CASE WHEN $6::smallint >= 2 THEN 'Ley 1712/2014 art. 19 (prueba)' END)
        RETURNING id
        """,
        tracking_number,
        origin_dept,
        origin_dept_code,
        dest_dept,
        dest_dept_code,
        nivel_seguridad,
    )
    assert row is not None
    return row["id"]


async def _seed_anexo(
    conn: asyncpg.Connection,
    schema: str,
    radicado_id: uuid.UUID,
    *,
    filename: str,
    mime_type: str,
    file_size: int,
    created_at: datetime,
) -> None:
    await conn.execute(
        f"""
        INSERT INTO "{schema}".anexos
            (radicado_id, file_id, filename, file_size, mime_type, created_at)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        radicado_id,
        uuid.uuid4(),
        filename,
        file_size,
        mime_type,
        created_at,
    )


@pytest.mark.integration
async def test_devuelve_la_dependencia_de_origen_y_el_formato_del_primer_anexo(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    rad_id = await _seed_radicado(conn, schema, tracking_number="2026-ITEST-E-000001")
    # DOS anexos, insertados en orden INVERSO al cronológico a propósito: si la
    # consulta no ordena, el que gana es el que la base devuelva primero, que
    # suele ser el último insertado. El documento principal es el más antiguo.
    await _seed_anexo(
        conn,
        schema,
        rad_id,
        filename="anexo-posterior.png",
        mime_type="image/png",
        file_size=999,
        created_at=datetime(2026, 3, 2, 10, 0, tzinfo=UTC),
    )
    await _seed_anexo(
        conn,
        schema,
        rad_id,
        filename="oficio.pdf",
        mime_type="application/pdf",
        file_size=2048,
        created_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
    )

    await conn.execute(f'SET search_path TO "{schema}", public')
    repo = DocumentRepository(conn, slug)
    meta = await repo.list_radicado_metadata_internal([rad_id], max_clearance=3)

    item = meta[rad_id]
    # La productora es la de ORIGEN. El destino existe en la misma fila, se
    # parece, y es el dato equivocado.
    assert item["origin_dept"] == "Oficina de Contratacion"
    assert item["origin_dept_code"] == 120
    assert item["origin_dept"] != "Archivo Central"
    # El formato es el del documento principal (el anexo más antiguo).
    assert item["formato"] == "application/pdf"
    assert item["tamano_bytes"] == 2048


@pytest.mark.integration
async def test_un_radicado_sin_anexos_no_inventa_formato(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """El `LEFT JOIN` es LEFT a propósito: un radicado sin anexos tiene que
    seguir apareciendo (con su asunto y su dependencia), no desaparecer del
    manifiesto. Un `JOIN` interno lo haría desaparecer en silencio."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    rad_id = await _seed_radicado(conn, schema, tracking_number="2026-ITEST-E-000002")

    await conn.execute(f'SET search_path TO "{schema}", public')
    repo = DocumentRepository(conn, slug)
    meta = await repo.list_radicado_metadata_internal([rad_id], max_clearance=3)

    assert rad_id in meta, "un radicado sin anexos no puede caerse del resultado"
    assert meta[rad_id]["formato"] is None
    assert meta[rad_id]["tamano_bytes"] is None
    assert meta[rad_id]["origin_dept"] == "Oficina de Contratacion"


@pytest.mark.integration
async def test_un_radicado_sin_dependencia_declarada_devuelve_vacio(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Control negativo: el vacío honesto es una respuesta válida. Una ingesta
    antigua o una radicación de ventanilla puede no traer dependencia de
    origen, y rellenarla con cualquier cosa la haría indistinguible de un dato
    real en un índice que se firma."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    rad_id = await _seed_radicado(
        conn,
        schema,
        tracking_number="2026-ITEST-E-000003",
        origin_dept=None,
        origin_dept_code=None,
    )

    await conn.execute(f'SET search_path TO "{schema}", public')
    repo = DocumentRepository(conn, slug)
    meta = await repo.list_radicado_metadata_internal([rad_id], max_clearance=3)

    assert meta[rad_id]["origin_dept"] is None
    assert meta[rad_id]["origin_dept_code"] is None


@pytest.mark.integration
async def test_el_no_read_up_sigue_recortando(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Las columnas nuevas no abren una puerta: un radicado por encima del
    clearance del llamante sigue sin aparecer — ni siquiera para decir de qué
    oficina viene (RF-SEG-08)."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    reservado = await _seed_radicado(
        conn,
        schema,
        tracking_number="2026-ITEST-E-000004",
        nivel_seguridad=3,
    )
    publico = await _seed_radicado(
        conn,
        schema,
        tracking_number="2026-ITEST-E-000005",
        nivel_seguridad=1,
    )

    await conn.execute(f'SET search_path TO "{schema}", public')
    repo = DocumentRepository(conn, slug)
    meta = await repo.list_radicado_metadata_internal([reservado, publico], max_clearance=1)

    assert publico in meta
    assert reservado not in meta


@pytest.mark.integration
async def test_devuelve_el_numero_de_radicado_y_la_tipologia_que_guarda_la_tabla(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """AREA-07-02 / AREA-06-08: archive-service coteja con esto lo que declara
    quien vincula. Sin la columna en el `SELECT`, la respuesta no traería el
    número y archive fallaría cerrado (503) para todo vínculo."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    rad_id = await _seed_radicado(conn, schema, tracking_number="2026-ITEST-E-000006")
    await conn.execute(
        f'UPDATE "{schema}".radicados SET doc_class = $2 WHERE id = $1', rad_id, "COR-PET"
    )

    await conn.execute(f'SET search_path TO "{schema}", public')
    meta = await DocumentRepository(conn, slug).list_radicado_metadata_internal(
        [rad_id], max_clearance=3
    )

    assert meta[rad_id]["tracking_number"] == "2026-ITEST-E-000006"
    assert meta[rad_id]["doc_class"] == "COR-PET"
