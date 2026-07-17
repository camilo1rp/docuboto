# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Escenario #1 (tenant-security-auditor) contra Postgres REAL.

Secuencia de fuga original (ver ingestion.py, docstring del módulo):
  1. `created(nivel=1)` → INSERT chunk nivel 1.
  2. `reclassified(nivel_nuevo=3)` con proveedor EXTERNO → antes: DELETE liso de
     la fila (rompe el candado GREATEST, que solo protege ON CONFLICT).
  3. `created` REDELIVERED tarde (at-least-once) → sin fila con la que hacer
     conflicto → INSERT plano al nivel 1: el radicado CLASIFICADA reaparecía a
     PUBLICA, recuperable por cualquier clearance, y su texto se reenviaba al
     proveedor externo.

Este test ejercita esa secuencia EXACTA contra un Postgres real (no mockeado):
la garantía es del motor — UNIQUE (source_type, source_ref) + ON CONFLICT +
GREATEST evaluados en el servidor — no del código Python. Con el fix (patrón
TOMBSTONE + nivel EFECTIVO en `ingest_radicado_created`), el `created` tardío del
paso 3 no reencarna nada: encuentra el tombstone a nivel 3, calcula
`nivel_efectivo = max(1, 3) = 3`, no es embebible con proveedor externo, y solo
refresca el tombstone (sin re-embeber, sin bajar el nivel).
"""

from __future__ import annotations

import asyncpg
import pytest

from app.core.config import settings
from app.repositories.knowledge_repository import KnowledgeRepository
from app.services import embeddings, ingestion

from .conftest import DSN

TRACKING = "2026-ITEST-E-000001"


@pytest.mark.integration
async def test_created_tardio_tras_reclassify_externo_no_reencarna_ni_reenvia(
    tenant_ctx: tuple[str, asyncpg.Connection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug, conn = tenant_ctx
    monkeypatch.setattr(settings, "embedding_provider", "openai")  # proveedor EXTERNO

    created_payload = {
        "tracking_number": TRACKING,
        "nivel_seguridad": 1,  # PUBLICA
        "subject": "Documento público inicial",
    }

    # 1) created(nivel=1): INSERT chunk nivel 1, embebido.
    await ingestion.ingest_radicado_created(conn, tenant_slug=slug, payload=created_payload)

    row = await conn.fetchrow(
        "SELECT nivel_seguridad, texto, embedding IS NULL AS tombstoned "
        "FROM knowledge_chunk WHERE source_type = 'radicado' AND source_ref = $1",
        TRACKING,
    )
    assert row is not None
    assert row["nivel_seguridad"] == 1
    assert row["tombstoned"] is False
    assert row["texto"] == "Documento público inicial"

    # 2) reclassified(nivel_nuevo=3) con proveedor externo: TOMBSTONE (no DELETE).
    await ingestion.apply_reclassification(
        conn,
        tenant_slug=slug,
        payload={"tracking_number": TRACKING, "nivel_nuevo": 3, "nivel_anterior": 1},
    )

    row = await conn.fetchrow(
        "SELECT nivel_seguridad, texto, embedding IS NULL AS tombstoned "
        "FROM knowledge_chunk WHERE source_type = 'radicado' AND source_ref = $1",
        TRACKING,
    )
    assert row is not None
    assert row["nivel_seguridad"] == 3
    assert row["tombstoned"] is True
    assert row["texto"] == ""

    # 3) created REDELIVERED tarde (mismo payload viejo, nivel=1 obsoleto): el fix
    #    debe leer el nivel EXISTENTE (3), calcular nivel_efectivo=max(1,3)=3, y
    #    NO reembeber / NO bajar el nivel — solo refrescar el tombstone.
    await ingestion.ingest_radicado_created(conn, tenant_slug=slug, payload=created_payload)

    row = await conn.fetchrow(
        "SELECT nivel_seguridad, texto, embedding IS NULL AS tombstoned "
        "FROM knowledge_chunk WHERE source_type = 'radicado' AND source_ref = $1",
        TRACKING,
    )
    assert row is not None, "la fila-centinela debe seguir existiendo (candado GREATEST)"
    assert row["nivel_seguridad"] == 3, "el nivel NUNCA debe bajar por un created tardío"
    assert row["tombstoned"] is True, "no debe reembeberse (no reenvío al proveedor externo)"
    assert row["texto"] == "", "el texto reclasificado no debe reaparecer en el índice"

    # No debe existir una SEGUNDA fila (el created tardío no debe haber insertado
    # un registro nuevo en paralelo al tombstone).
    count = await conn.fetchval(
        "SELECT count(*) FROM knowledge_chunk WHERE source_type = 'radicado' AND source_ref = $1",
        TRACKING,
    )
    assert count == 1

    # 4) search-leak cerrado: ni con el clearance más bajo ni con el más alto el
    #    tombstone es un resultado de búsqueda (embedding IS NULL lo excluye
    #    siempre, sea cual sea nivel_seguridad/clearance).
    literal = embeddings.to_pgvector(embeddings.embed("Documento público inicial"))
    repo = KnowledgeRepository(conn)
    for clearance in (1, 2, 3):
        results = await repo.search(
            embedding_literal=literal, top_k=10, max_clearance=clearance, acl_filter=None
        )
        assert results == [], f"tombstone visible en búsqueda con clearance={clearance}"


@pytest.mark.integration
async def test_created_tardio_tras_reclassify_stub_actualiza_nivel_sin_tombstone(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Regresión (candado ya existente): con proveedor `stub` (default), subir de
    nivel por reclasificación sigue usando `update_nivel_by_source` (nivel exacto,
    fila viva con embedding) — el patrón tombstone es SOLO para proveedor externo.
    Un `created` tardío posterior respeta el nivel vía nivel EFECTIVO también en
    esta rama."""
    slug, conn = tenant_ctx
    assert settings.embedding_provider == "stub"

    created_payload = {
        "tracking_number": TRACKING,
        "nivel_seguridad": 1,
        "subject": "Documento público",
    }
    await ingestion.ingest_radicado_created(conn, tenant_slug=slug, payload=created_payload)
    await ingestion.apply_reclassification(
        conn,
        tenant_slug=slug,
        payload={"tracking_number": TRACKING, "nivel_nuevo": 3, "nivel_anterior": 1},
    )

    row = await conn.fetchrow(
        "SELECT nivel_seguridad, embedding IS NULL AS tombstoned "
        "FROM knowledge_chunk WHERE source_type = 'radicado' AND source_ref = $1",
        TRACKING,
    )
    assert row is not None
    assert row["nivel_seguridad"] == 3
    assert row["tombstoned"] is False  # stub: fila viva, no tombstoneada

    # created tardío (nivel=1 obsoleto): nivel_efectivo=max(1,3)=3; con stub
    # is_embeddable siempre es True, así que SÍ re-embebe, pero al nivel efectivo
    # (3) — GREATEST en upsert_chunk no permite que baje.
    await ingestion.ingest_radicado_created(conn, tenant_slug=slug, payload=created_payload)

    row = await conn.fetchrow(
        "SELECT nivel_seguridad FROM knowledge_chunk "
        "WHERE source_type = 'radicado' AND source_ref = $1",
        TRACKING,
    )
    assert row is not None
    assert row["nivel_seguridad"] == 3


@pytest.mark.integration
async def test_second_connection_sees_tombstone_durably(
    tenant_ctx: tuple[str, asyncpg.Connection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Durabilidad real (no solo visibilidad intra-conexión): se lee desde una
    SEGUNDA conexión `asyncpg` — el mismo patrón que el smoke test de
    archive-service."""
    slug, conn = tenant_ctx
    monkeypatch.setattr(settings, "embedding_provider", "openai")

    await ingestion.ingest_radicado_created(
        conn,
        tenant_slug=slug,
        payload={"tracking_number": TRACKING, "nivel_seguridad": 1, "subject": "x"},
    )
    await ingestion.apply_reclassification(
        conn,
        tenant_slug=slug,
        payload={"tracking_number": TRACKING, "nivel_nuevo": 3, "nivel_anterior": 1},
    )

    from app.core.database import _schema as tenant_schema

    schema = tenant_schema(slug)
    second_conn = await asyncpg.connect(dsn=DSN)
    try:
        await second_conn.execute(f'SET search_path = "{schema}", public')
        row = await second_conn.fetchrow(
            "SELECT nivel_seguridad, embedding IS NULL AS tombstoned "
            "FROM knowledge_chunk WHERE source_type = 'radicado' AND source_ref = $1",
            TRACKING,
        )
    finally:
        await second_conn.close()

    assert row is not None
    assert row["nivel_seguridad"] == 3
    assert row["tombstoned"] is True
