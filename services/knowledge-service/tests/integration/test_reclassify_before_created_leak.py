# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Residual ALTA (config stub por defecto) + residual MEDIA, gemelos del residual
que ``test_reclassify_tombstone_leak.py`` ya cerró para la rama de proveedor
EXTERNO, pero en la rama STUB/local (o sin proveedor externo).

Secuencia de fuga original (ver ``KnowledgeRepository.update_nivel_by_source``,
docstring): `update_nivel_by_source` era un ``UPDATE`` PLANO. Si
`document.radicado.reclassified` llegaba ANTES que el `created` original (p. ej.
el `created` falló un embed transitorio y `reclaim_stale` lo recuperó DESPUÉS), el
UPDATE afectaba 0 filas — el nivel del `reclassified` se perdía sin dejar rastro,
y el `created` tardío insertaba el chunk PLANO a su nivel de nacimiento (nivel de
nacimiento, normalmente PUBLICA), sub-clasificándolo. Con el fix,
`update_nivel_by_source` es también un UPSERT-con-centinela: deja una fila
centinela (``embedding = NULL``) cuando la fila no existe, para que el `created`
posterior SIEMPRE encuentre con qué comparar (nivel EFECTIVO,
``ingestion.ingest_radicado_created``).

Estos tests ejercitan esa secuencia EXACTA contra Postgres REAL — la garantía es
del motor (``UNIQUE (source_type, source_ref)`` + ``ON CONFLICT`` evaluados en el
servidor), no del código Python (ver [[tier-integracion-atomicidad]] en memoria de
proyecto).
"""

from __future__ import annotations

import asyncpg
import pytest

from app.core.config import settings
from app.repositories.knowledge_repository import KnowledgeRepository
from app.services import embeddings, ingestion

TRACKING = "2026-ITEST-E-000002"


async def _fetch_chunk(conn: asyncpg.Connection, tracking: str = TRACKING) -> asyncpg.Record:
    row = await conn.fetchrow(
        "SELECT nivel_seguridad, texto, embedding IS NULL AS tombstoned "
        "FROM knowledge_chunk WHERE source_type = 'radicado' AND source_ref = $1",
        tracking,
    )
    assert row is not None
    return row


@pytest.mark.integration
async def test_reclassified_antes_de_created_stub_embebe_a_nivel_efectivo(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Proveedor STUB (default): reclassified(3) llega SIN chunk previo → deja un
    centinela nivel 3 (embedding NULL, invisible en search). El created(1) tardío
    posterior NO reencarna el chunk a PUBLICA: lo embebe al nivel EFECTIVO (3)."""
    slug, conn = tenant_ctx
    assert settings.embedding_provider == "stub"

    # 1) reclassified(nivel_nuevo=3) SIN created previo: update_nivel_by_source
    #    toma la rama INSERT (fila ausente) y deja un centinela.
    await ingestion.apply_reclassification(
        conn,
        tenant_slug=slug,
        payload={"tracking_number": TRACKING, "nivel_nuevo": 3, "nivel_anterior": None},
    )

    row = await _fetch_chunk(conn)
    assert row["nivel_seguridad"] == 3
    assert row["tombstoned"] is True, "el centinela debe tener embedding NULL"
    assert row["texto"] == ""

    # No debe existir una segunda fila.
    count = await conn.fetchval(
        "SELECT count(*) FROM knowledge_chunk WHERE source_type = 'radicado' AND source_ref = $1",
        TRACKING,
    )
    assert count == 1

    # 2) created(nivel=1) llega DESPUÉS (evento original, nivel de nacimiento bajo):
    #    nivel_efectivo = max(1, 3) = 3 → con stub es embebible → SE EMBEBE, pero
    #    al nivel efectivo (GREATEST en upsert_chunk impide que baje a 1).
    await ingestion.ingest_radicado_created(
        conn,
        tenant_slug=slug,
        payload={
            "tracking_number": TRACKING,
            "nivel_seguridad": 1,
            "subject": "Documento que nace público pero ya fue reclasificado",
        },
    )

    row = await _fetch_chunk(conn)
    assert row["nivel_seguridad"] == 3, "el created tardío NUNCA debe bajar el nivel"
    assert row["tombstoned"] is False, "con stub sí se embebe, al nivel efectivo"
    assert row["texto"] == "Documento que nace público pero ya fue reclasificado"

    count = await conn.fetchval(
        "SELECT count(*) FROM knowledge_chunk WHERE source_type = 'radicado' AND source_ref = $1",
        TRACKING,
    )
    assert count == 1, "el created tardío no debe insertar una fila en paralelo al centinela"

    # 3) search: a clearance 1 (PUBLICA) el chunk (nivel 3) NO debe aparecer; a
    #    clearance 3 (CLASIFICADA) sí.
    literal = embeddings.to_pgvector(
        embeddings.embed("Documento que nace público pero ya fue reclasificado")
    )
    repo = KnowledgeRepository(conn)
    results_low = await repo.search(
        embedding_literal=literal, top_k=10, max_clearance=1, acl_filter=None
    )
    assert results_low == [], "el chunk sub-clasificado NO debe ser visible a clearance 1"

    results_high = await repo.search(
        embedding_literal=literal, top_k=10, max_clearance=3, acl_filter=None
    )
    assert any(r["source_ref"] == TRACKING for r in results_high), (
        "el chunk SÍ debe ser visible a clearance 3 (su nivel efectivo real)"
    )


@pytest.mark.integration
async def test_reclassified_antes_de_created_externo_no_embebe_nunca(
    tenant_ctx: tuple[str, asyncpg.Connection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proveedor EXTERNO: reclassified(3) SIN chunk previo → tombstone_by_source
    deja el centinela (misma UPSERT que ya protegía esta rama). El created(1)
    tardío posterior calcula nivel_efectivo=3, is_embeddable(3, externo)=False →
    NUNCA se embebe ni se reenvía el texto al proveedor externo; sigue
    tombstoneado y no aparece en ningún clearance."""
    slug, conn = tenant_ctx
    monkeypatch.setattr(settings, "embedding_provider", "openai")

    await ingestion.apply_reclassification(
        conn,
        tenant_slug=slug,
        payload={"tracking_number": TRACKING, "nivel_nuevo": 3, "nivel_anterior": None},
    )

    row = await _fetch_chunk(conn)
    assert row["nivel_seguridad"] == 3
    assert row["tombstoned"] is True
    assert row["texto"] == ""

    await ingestion.ingest_radicado_created(
        conn,
        tenant_slug=slug,
        payload={
            "tracking_number": TRACKING,
            "nivel_seguridad": 1,
            "subject": "contenido que jamás debe salir a un proveedor externo",
        },
    )

    row = await _fetch_chunk(conn)
    assert row["nivel_seguridad"] == 3
    assert row["tombstoned"] is True, "no debe reembeberse: guard sobre el nivel EFECTIVO (3)"
    assert row["texto"] == ""

    literal = embeddings.to_pgvector(
        embeddings.embed("contenido que jamás debe salir a un proveedor externo")
    )
    repo = KnowledgeRepository(conn)
    for clearance in (1, 2, 3):
        results = await repo.search(
            embedding_literal=literal, top_k=10, max_clearance=clearance, acl_filter=None
        )
        assert results == [], f"tombstone visible en búsqueda con clearance={clearance}"


@pytest.mark.integration
async def test_desclasificacion_con_fila_existente_baja_nivel_exacto(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Fila YA existente a nivel 3 (CLASIFICADA, embebida — stub siempre embebe):
    reclassified(1) debe fijar el nivel EXACTO a 1 (rama CONFLICT, SIN GREATEST —
    una desclasificación legítima debe poder BAJAR el nivel), conservando el
    contenido, que vuelve a ser buscable a clearance 1."""
    slug, conn = tenant_ctx
    assert settings.embedding_provider == "stub"

    await ingestion.ingest_radicado_created(
        conn,
        tenant_slug=slug,
        payload={
            "tracking_number": TRACKING,
            "nivel_seguridad": 3,
            "subject": "Documento clasificado que luego se desclasifica",
        },
    )
    row = await _fetch_chunk(conn)
    assert row["nivel_seguridad"] == 3
    assert row["tombstoned"] is False

    await ingestion.apply_reclassification(
        conn,
        tenant_slug=slug,
        payload={"tracking_number": TRACKING, "nivel_nuevo": 1, "nivel_anterior": 3},
    )

    row = await _fetch_chunk(conn)
    assert row["nivel_seguridad"] == 1, "GREATEST NO debe aplicar aquí: es una baja legítima"
    assert row["tombstoned"] is False
    assert row["texto"] == "Documento clasificado que luego se desclasifica"

    literal = embeddings.to_pgvector(
        embeddings.embed("Documento clasificado que luego se desclasifica")
    )
    repo = KnowledgeRepository(conn)
    results = await repo.search(
        embedding_literal=literal, top_k=10, max_clearance=1, acl_filter=None
    )
    assert any(r["source_ref"] == TRACKING for r in results), (
        "tras la desclasificación, el chunk debe ser buscable incluso a clearance 1"
    )


@pytest.mark.integration
async def test_reclassify_sin_nivel_nuevo_deja_asiento_de_auditoria(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """#Media: un `reclassified` malformado (sin `nivel_nuevo`) fuerza fail-close a
    CLASIFICADA (3) Y deja un asiento en `audit_log` — no solo un log de
    aplicación. Best-effort, pero en la ruta feliz (Postgres real disponible) el
    asiento debe existir."""
    slug, conn = tenant_ctx

    await ingestion.apply_reclassification(
        conn,
        tenant_slug=slug,
        payload={"tracking_number": TRACKING, "nivel_anterior": 1},  # sin nivel_nuevo
    )

    row = await _fetch_chunk(conn)
    assert row["nivel_seguridad"] == 3

    audit_row = await conn.fetchrow(
        """
        SELECT action, object_type, object_ref, actor, payload
          FROM public.audit_log
         WHERE tenant_slug = $1
           AND action = 'knowledge.reclassify_malformed_failclose'
           AND object_ref = $2
        """,
        slug,
        TRACKING,
    )
    assert audit_row is not None, "el fail-close malformado debe dejar traza en audit_log"
    assert audit_row["object_type"] == "radicado"
    assert audit_row["actor"] == "system"
