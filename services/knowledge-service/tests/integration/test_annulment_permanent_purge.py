# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Residual "purga-en-anulación" (E21, hallazgo archival-compliance-auditor): un
radicado ANULADO seguía siendo consultable en el índice semántico de
knowledge-service. Cierre: `document.radicado.annulled` (nuevo evento, emitido por
`AnulacionService._emit_annulled` al aprobar la anulación) → `void_by_source`
(migración 006, columna `voided`) purga PERMANENTEMENTE el chunk.

Estos tests ejercitan las TRES órdenes posibles de entrega created↔annulled contra
Postgres REAL — la garantía de permanencia es del motor (`UNIQUE (source_type,
source_ref)` + `ON CONFLICT` + el guard `voided` leído ANTES de decidir si se
embebe), no del código Python (ver [[tier-integracion-atomicidad]] en memoria de
proyecto). Espejo directo de `test_reclassify_before_created_leak.py`, pero para
un marcador PERMANENTE (a diferencia del tombstone por nivel, que sí es
reversible en una desclasificación)."""

from __future__ import annotations

import asyncpg
import pytest

from app.core.config import settings
from app.repositories.knowledge_repository import KnowledgeRepository
from app.services import embeddings, ingestion

TRACKING = "2026-ITEST-E-000003"
TRACKING_OK = "2026-ITEST-E-000004"


async def _fetch_chunk(conn: asyncpg.Connection, tracking: str = TRACKING) -> asyncpg.Record:
    row = await conn.fetchrow(
        "SELECT nivel_seguridad, texto, voided, embedding IS NULL AS tombstoned "
        "FROM knowledge_chunk WHERE source_type = 'radicado' AND source_ref = $1",
        tracking,
    )
    assert row is not None
    return row


@pytest.mark.integration
async def test_created_luego_annulled_purga_y_desaparece_de_busqueda(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Orden normal: created indexa, annulled purga PERMANENTEMENTE (voided=true,
    material vaciado) y el chunk deja de ser buscable a cualquier clearance."""
    slug, conn = tenant_ctx
    assert settings.embedding_provider == "stub"

    await ingestion.ingest_radicado_created(
        conn,
        tenant_slug=slug,
        payload={
            "tracking_number": TRACKING,
            "nivel_seguridad": 1,
            "subject": "Documento que será anulado",
        },
    )
    row = await _fetch_chunk(conn)
    assert row["voided"] is False
    assert row["tombstoned"] is False

    await ingestion.void_radicado(
        conn, tenant_slug=slug, payload={"id": "doc-1", "tracking_number": TRACKING}
    )

    row = await _fetch_chunk(conn)
    assert row["voided"] is True
    assert row["tombstoned"] is True, "el material derivado debe quedar vacío"
    assert row["texto"] == ""

    literal = embeddings.to_pgvector(embeddings.embed("Documento que será anulado"))
    repo = KnowledgeRepository(conn)
    for clearance in (1, 2, 3):
        results = await repo.search(
            embedding_literal=literal, top_k=10, max_clearance=clearance, acl_filter=None
        )
        assert not any(r["source_ref"] == TRACKING for r in results), (
            f"el radicado anulado NO debe ser buscable a clearance={clearance}"
        )


@pytest.mark.integration
async def test_annulled_antes_de_created_deja_centinela_y_created_tardio_skipea(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Reordenamiento inverso: `annulled` llega SIN `created` previo (p. ej. el
    `created` original falló un embed transitorio y quedó reintentando en la
    PEL). `void_by_source` deja un centinela voided desde el nacimiento — el
    `created` tardío que llega DESPUÉS NUNCA debe indexar: SKIP total, el chunk
    sigue voided y sin contenido."""
    slug, conn = tenant_ctx

    await ingestion.void_radicado(
        conn, tenant_slug=slug, payload={"id": "doc-2", "tracking_number": TRACKING}
    )

    row = await _fetch_chunk(conn)
    assert row["voided"] is True
    assert row["tombstoned"] is True
    assert row["nivel_seguridad"] == 3, "fail-safe al nivel máximo (CLASIFICADA)"

    count = await conn.fetchval(
        "SELECT count(*) FROM knowledge_chunk WHERE source_type = 'radicado' AND source_ref = $1",
        TRACKING,
    )
    assert count == 1, "no debe existir una segunda fila"

    # created tardío (evento original, redelivery at-least-once) llega DESPUÉS.
    await ingestion.ingest_radicado_created(
        conn,
        tenant_slug=slug,
        payload={
            "tracking_number": TRACKING,
            "nivel_seguridad": 1,
            "subject": "contenido que jamás debe indexarse",
        },
    )

    row = await _fetch_chunk(conn)
    assert row["voided"] is True, "el created tardío NUNCA debe des-voidear"
    assert row["tombstoned"] is True, "el created tardío NUNCA debe reembeber"
    assert row["texto"] == ""

    count = await conn.fetchval(
        "SELECT count(*) FROM knowledge_chunk WHERE source_type = 'radicado' AND source_ref = $1",
        TRACKING,
    )
    assert count == 1, "el created tardío no debe insertar una fila en paralelo al centinela"

    literal = embeddings.to_pgvector(embeddings.embed("contenido que jamás debe indexarse"))
    repo = KnowledgeRepository(conn)
    results = await repo.search(
        embedding_literal=literal, top_k=10, max_clearance=3, acl_filter=None
    )
    assert results == [], "el centinela voided nunca debe aparecer en búsqueda"


@pytest.mark.integration
async def test_annulled_luego_created_redelivered_sigue_voided(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Orden normal (created→annulled) seguido de un REDELIVERY tardío del mismo
    `created` (at-least-once, p. ej. `reclaim_stale` reintentando un mensaje que
    ya había sido procesado con éxito la primera vez): la purga sigue siendo
    permanente — el redelivery no reindexa ni des-voidea."""
    slug, conn = tenant_ctx

    await ingestion.ingest_radicado_created(
        conn,
        tenant_slug=slug,
        payload={
            "tracking_number": TRACKING,
            "nivel_seguridad": 1,
            "subject": "Documento que será anulado y luego redelivered",
        },
    )
    await ingestion.void_radicado(
        conn, tenant_slug=slug, payload={"id": "doc-3", "tracking_number": TRACKING}
    )
    row = await _fetch_chunk(conn)
    assert row["voided"] is True

    # Redelivery tardío del created ORIGINAL (mismo evento, reprocesado).
    await ingestion.ingest_radicado_created(
        conn,
        tenant_slug=slug,
        payload={
            "tracking_number": TRACKING,
            "nivel_seguridad": 1,
            "subject": "Documento que será anulado y luego redelivered",
        },
    )

    row = await _fetch_chunk(conn)
    assert row["voided"] is True, "el redelivery del created no debe des-voidear"
    assert row["tombstoned"] is True
    assert row["texto"] == ""


@pytest.mark.integration
async def test_radicado_no_anulado_sigue_buscable_sin_regresion(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """No-regresión: un radicado que NUNCA fue anulado (voided=false, el DEFAULT
    de la migración 006) sigue siendo buscable con normalidad."""
    slug, conn = tenant_ctx

    await ingestion.ingest_radicado_created(
        conn,
        tenant_slug=slug,
        payload={
            "tracking_number": TRACKING_OK,
            "nivel_seguridad": 1,
            "subject": "Documento vigente que nunca se anula",
        },
    )

    row = await _fetch_chunk(conn, TRACKING_OK)
    assert row["voided"] is False
    assert row["tombstoned"] is False

    literal = embeddings.to_pgvector(embeddings.embed("Documento vigente que nunca se anula"))
    repo = KnowledgeRepository(conn)
    results = await repo.search(
        embedding_literal=literal, top_k=10, max_clearance=1, acl_filter=None
    )
    assert any(r["source_ref"] == TRACKING_OK for r in results), (
        "un radicado NO anulado debe seguir siendo buscable"
    )
