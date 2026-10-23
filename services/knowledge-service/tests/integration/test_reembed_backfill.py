# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`app/ops/reembed.py` (E21 Inc.1, backfill de la migración 007) contra
Postgres REAL — ejercita `_reembed_tenant` directamente (la unidad de trabajo
por-schema de `run()`), sin depender de qué OTROS schemas `tenant_*` existan en
la base en el momento del test (`run()` enumera TODOS los schemas `tenant_%` de
`information_schema.schemata`, algo que este archivo deliberadamente no
ejercita para no volverse sensible al estado global de la BD compartida — ver
[[tier-integracion-atomicidad]] en memoria de proyecto).

Cubre:
  - Re-embebe un centinela pendiente (embedding NULL, texto no vacío) con el
    proveedor `stub` y lo saca de `list_pending_reembed_ids`.
  - Idempotente: una segunda pasada sobre el mismo tenant no encuentra nada
    pendiente y no vuelve a tocar la fila.
  - Respeta `is_embeddable` (D-02/RF-SEG-08): un centinela Clasificado con el
    proveedor CONFIGURADO en modo externo queda BLOQUEADO (sigue con
    `embedding IS NULL`, nunca se le construye/llama al proveedor real) y deja
    traza `knowledge.reembed_skipped_classified` en `audit_log`.
  - Aislamiento por tenant: una llamada scoped a un schema NO toca el
    centinela pendiente de OTRO schema, aunque ambos existan simultáneamente
    en la misma base — la scoping la hace `_reembed_tenant` fijando su propio
    `search_path` por llamada, no una condición WHERE por tenant_slug.
"""

from __future__ import annotations

import json

import asyncpg
import pytest

from app.core.clearance import CLASIFICADA, PUBLICA
from app.core.config import settings
from app.core.database import _schema as tenant_schema
from app.ops import reembed
from app.repositories.knowledge_repository import KnowledgeRepository
from app.services.embeddings import StubEmbeddingProvider, to_pgvector

pytestmark = pytest.mark.integration


async def _seed_pending_sentinel(
    conn: asyncpg.Connection, *, source_ref: str, nivel: int, texto: str
) -> None:
    """Simula el estado POST-migración-007: una fila viva (texto real, nivel
    real) pero con `embedding = NULL` — exactamente lo que deja la migración
    007 sobre datos preexistentes, y lo que `list_pending_reembed_ids` recoge."""
    repo = KnowledgeRepository(conn)
    provider = StubEmbeddingProvider()
    await repo.upsert_chunk(
        source_type="radicado",
        source_ref=source_ref,
        texto=texto,
        embedding_literal=to_pgvector(provider.embed_query(texto)),
        metadata={},
        acl={},
        nivel_seguridad=nivel,
    )
    await conn.execute(
        "UPDATE knowledge_chunk SET embedding = NULL WHERE source_type = 'radicado' AND source_ref = $1",
        source_ref,
    )


@pytest.mark.integration
async def test_reembed_backfills_pending_sentinel_and_is_idempotent(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    assert settings.embedding_provider == "stub"
    repo = KnowledgeRepository(conn)

    await _seed_pending_sentinel(
        conn, source_ref="RE-1", nivel=PUBLICA, texto="contenido publico pendiente de re-embed"
    )
    pending_before = await repo.list_pending_reembed_ids()
    assert len(pending_before) == 1

    provider = StubEmbeddingProvider()
    hechas, bloqueadas, fallidas = await reembed._reembed_tenant(
        conn,
        schema=tenant_schema(slug),
        slug=slug,
        batch_size=16,
        dry_run=False,
        provider=provider,
    )
    assert (hechas, bloqueadas, fallidas) == (1, 0, 0)

    row = await conn.fetchrow(
        "SELECT embedding IS NOT NULL AS has_embedding FROM knowledge_chunk WHERE source_ref = 'RE-1'"
    )
    assert row["has_embedding"] is True

    # Idempotencia: nada pendiente en la segunda pasada.
    pending_after = await repo.list_pending_reembed_ids()
    assert pending_after == []

    hechas_2, bloqueadas_2, fallidas_2 = await reembed._reembed_tenant(
        conn,
        schema=tenant_schema(slug),
        slug=slug,
        batch_size=16,
        dry_run=False,
        provider=provider,
    )
    assert (hechas_2, bloqueadas_2, fallidas_2) == (0, 0, 0)


@pytest.mark.integration
async def test_reembed_dry_run_counts_without_touching_rows(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    await _seed_pending_sentinel(
        conn, source_ref="RE-DRY-1", nivel=PUBLICA, texto="contenido dry-run"
    )

    hechas, bloqueadas, fallidas = await reembed._reembed_tenant(
        conn,
        schema=tenant_schema(slug),
        slug=slug,
        batch_size=16,
        dry_run=True,
        provider=None,
    )
    assert (hechas, bloqueadas, fallidas) == (1, 0, 0)

    row = await conn.fetchrow(
        "SELECT embedding IS NULL AS still_pending FROM knowledge_chunk WHERE source_ref = 'RE-DRY-1'"
    )
    assert row["still_pending"] is True, "dry-run NO debe escribir ningún embedding"


@pytest.mark.integration
async def test_reembed_respects_is_embeddable_blocks_classified_with_external_provider(
    tenant_ctx: tuple[str, asyncpg.Connection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-02/RF-SEG-08: un centinela Clasificado NUNCA se re-embebe si el
    proveedor CONFIGURADO no es local — la fila queda bloqueada (embedding
    sigue NULL) y se deja traza auditada. Usa `"ollama"` como stand-in del
    proveedor externo (igual que `tests/test_ingestion.py`): al estar
    BLOQUEADO por `is_embeddable` ANTES de llamar a `provider.embed_documents`,
    nunca se construye/llama un cliente HTTP real — el `provider` pasado a
    `_reembed_tenant` es un stub inerte que jamás debería ejecutarse en esta
    rama (si se llamara, el test fallaría por un side-effect no esperado)."""
    slug, conn = tenant_ctx
    monkeypatch.setattr(settings, "embedding_provider", "ollama")

    # ADR-037: con la bandera apagada el tenant se salta ANTES de llegar a `is_embeddable`;
    # esta prueba es del bloqueo por residencia, así que se abre esa puerta.
    async def _bandera_abierta(*_a, **_k) -> bool:
        return False

    monkeypatch.setattr(reembed, "egreso_con_bandera_apagada", _bandera_abierta)

    await _seed_pending_sentinel(
        conn, source_ref="RE-2", nivel=CLASIFICADA, texto="contenido clasificado pendiente"
    )

    def _boom(*_args, **_kwargs):  # pragma: no cover - no debe invocarse
        raise AssertionError("no debe llamarse al proveedor externo para material bloqueado")

    inert_provider = StubEmbeddingProvider()
    inert_provider.embed_documents = _boom  # type: ignore[method-assign]

    hechas, bloqueadas, fallidas = await reembed._reembed_tenant(
        conn,
        schema=tenant_schema(slug),
        slug=slug,
        batch_size=16,
        dry_run=False,
        provider=inert_provider,
    )
    assert (hechas, bloqueadas, fallidas) == (0, 1, 0)

    row = await conn.fetchrow(
        "SELECT embedding IS NULL AS still_blocked FROM knowledge_chunk WHERE source_ref = 'RE-2'"
    )
    assert row["still_blocked"] is True

    audit_row = await conn.fetchrow(
        """
        SELECT action, object_ref, payload
          FROM public.audit_log
         WHERE tenant_slug = $1
           AND action = 'knowledge.reembed_skipped_classified'
           AND object_ref = 'RE-2'
        """,
        slug,
    )
    assert audit_row is not None, "el bloqueo por residencia debe dejar traza en audit_log"
    # asyncpg no decodifica JSONB a dict sin un codec registrado (ninguno lo
    # registra aquí, ver app/core/database.py): el `payload` vuelve como texto
    # crudo — mismo patrón que el resto de la suite (`json.loads(row["payload"])`,
    # p. ej. archive-service/tests/integration/test_indice_reconciliation_atomicity.py).
    payload = json.loads(audit_row["payload"])
    assert payload["nivel_seguridad"] == CLASIFICADA


@pytest.mark.integration
async def test_reembed_local_st_provider_name_is_never_blocked(
    tenant_ctx: tuple[str, asyncpg.Connection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gemelo positivo del test anterior: con `local_st` CONFIGURADO como
    nombre de proveedor (aunque aquí se inyecta el objeto `StubEmbeddingProvider`
    para no cargar el modelo real de 400MB — `_reembed_tenant` recibe el
    `provider` YA CONSTRUIDO por el llamante, `is_embeddable` solo consulta el
    NOMBRE vía `settings.embedding_provider`), un centinela Clasificado SÍ se
    re-embebe: `local_st` es local/soberano (E21 Inc.1)."""
    slug, conn = tenant_ctx
    monkeypatch.setattr(settings, "embedding_provider", "local_st")

    await _seed_pending_sentinel(
        conn, source_ref="RE-3", nivel=CLASIFICADA, texto="contenido clasificado, proveedor local"
    )

    provider = StubEmbeddingProvider()  # doble ligero, is_embeddable no mira el tipo del objeto
    hechas, bloqueadas, fallidas = await reembed._reembed_tenant(
        conn,
        schema=tenant_schema(slug),
        slug=slug,
        batch_size=16,
        dry_run=False,
        provider=provider,
    )
    assert (hechas, bloqueadas, fallidas) == (1, 0, 0)

    row = await conn.fetchrow(
        "SELECT embedding IS NOT NULL AS has_embedding FROM knowledge_chunk WHERE source_ref = 'RE-3'"
    )
    assert row["has_embedding"] is True


@pytest.mark.integration
async def test_reembed_does_not_touch_tombstones_voided_or_already_embedded(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    repo = KnowledgeRepository(conn)
    provider = StubEmbeddingProvider()

    # Tombstone real (texto = '', embedding NULL) — NUNCA debe re-embeberse.
    await repo.tombstone_by_source("radicado", "RE-TOMB", CLASIFICADA)
    # Voided (purga permanente) — NUNCA debe re-embeberse.
    await repo.void_by_source("radicado", "RE-VOID")
    # Fila YA embebida — no debe tocarse (no está en list_pending_reembed_ids).
    await repo.upsert_chunk(
        source_type="radicado",
        source_ref="RE-YA-EMBEBIDA",
        texto="ya tiene embedding",
        embedding_literal=to_pgvector(provider.embed_query("ya tiene embedding")),
        metadata={},
        acl={},
        nivel_seguridad=PUBLICA,
    )

    pending = await repo.list_pending_reembed_ids()
    assert pending == [], "tombstones/voided/ya-embebidas no cuentan como pendientes"

    hechas, bloqueadas, fallidas = await reembed._reembed_tenant(
        conn,
        schema=tenant_schema(slug),
        slug=slug,
        batch_size=16,
        dry_run=False,
        provider=provider,
    )
    assert (hechas, bloqueadas, fallidas) == (0, 0, 0)


@pytest.mark.integration
async def test_reembed_tenant_isolation_scoped_call_never_touches_other_schema(
    db_conn: asyncpg.Connection,
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Aislamiento multi-tenant: dos schemas con un centinela pendiente CADA
    UNO; una llamada a `_reembed_tenant` SCOPED al schema A no debe re-embeber
    (ni tocar de ninguna forma) el centinela del schema B — la scoping vive en
    que `_reembed_tenant` fija su propio `search_path` al `schema` recibido por
    parámetro antes de leer/escribir nada."""
    slug_a, conn = tenant_ctx  # conn == db_conn (mismo objeto, ver conftest)
    schema_a = tenant_schema(slug_a)

    slug_b = f"itest-{slug_a.removeprefix('itest-')}-b"
    schema_b = tenant_schema(slug_b)
    await db_conn.execute(f'CREATE SCHEMA "{schema_b}"')
    try:
        from migrations import runner

        for path in runner._sql_files(runner.TENANT_DIR):
            async with db_conn.transaction():
                await db_conn.execute(f'SET LOCAL search_path = "{schema_b}"')
                await db_conn.execute(path.read_text())

        # Sembrar el centinela de B con el search_path puesto en B.
        await db_conn.execute(f'SET search_path = "{schema_b}"')
        await _seed_pending_sentinel(
            db_conn, source_ref="RE-B-1", nivel=PUBLICA, texto="contenido del tenant B"
        )

        # Volver el search_path activo a A (estado que `tenant_ctx` dejó) y
        # sembrar el centinela de A.
        await db_conn.execute(f'SET search_path = "{schema_a}"')
        await _seed_pending_sentinel(
            db_conn, source_ref="RE-A-1", nivel=PUBLICA, texto="contenido del tenant A"
        )

        provider = StubEmbeddingProvider()
        hechas, bloqueadas, fallidas = await reembed._reembed_tenant(
            db_conn,
            schema=schema_a,
            slug=slug_a,
            batch_size=16,
            dry_run=False,
            provider=provider,
        )
        assert (hechas, bloqueadas, fallidas) == (1, 0, 0)

        row_a = await db_conn.fetchrow(
            f'SELECT embedding IS NOT NULL AS has_embedding FROM "{schema_a}".knowledge_chunk '
            "WHERE source_ref = 'RE-A-1'"
        )
        assert row_a["has_embedding"] is True

        row_b = await db_conn.fetchrow(
            f'SELECT embedding IS NULL AS still_pending FROM "{schema_b}".knowledge_chunk '
            "WHERE source_ref = 'RE-B-1'"
        )
        assert row_b["still_pending"] is True, (
            "un _reembed_tenant scoped al schema A NUNCA debe tocar el schema B"
        )
    finally:
        assert schema_b.startswith("tenant_itest_"), (
            f"guardarraíl: me niego a hacer DROP SCHEMA CASCADE de {schema_b!r}"
        )
        await db_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_b}" CASCADE')
        await db_conn.execute(f'SET search_path = "{schema_a}"')
