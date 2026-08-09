# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Re-embedding / backfill del índice de conocimiento (E21 Inc.1, migración 007).

La migración 007 (`vector(64)` → `vector(384)`, adopción de `local_st` como
proveedor real) NULLea los `embedding` existentes — quedan como CENTINELAS
(mismo patrón que un tombstone) hasta que este job los repuebla. `search` ya
los excluye (`embedding IS NOT NULL`), así que hasta correr este backfill el
índice semántico está efectivamente vacío para cualquier chunk pre-existente.

CC-01 (capa DERIVADA, reconstruible — NO es dato maestro): el `texto` de cada
chunk YA está persistido en `knowledge_chunk` (lo dejó la ingesta original);
este job NUNCA vuelve a document-service ni a ninguna fuente — solo relee
`texto` y produce un vector nuevo con el proveedor ACTUALMENTE configurado
(`EMBEDDING_PROVIDER`).

Canal de OPS, NO endpoint HTTP (familia de `auth-service/app/ops/*`):

    python -m app.ops.reembed --dry-run                # cuenta lo pendiente/bloqueado
    python -m app.ops.reembed                           # re-embebe (todos los tenants)
    python -m app.ops.reembed --tenant icetex            # solo un tenant
    python -m app.ops.reembed --batch-size 32            # tamaño de lote de embed_documents

INVARIANTES (fail-closed):
 - Guard de dimensión IDÉNTICO al de `app/main.py::lifespan`: si
   `get_provider().dim != settings.embedding_dim`, aborta ANTES de tocar
   ninguna fila (evita sembrar vectores de la dimensión equivocada, que
   `ALTER TYPE`/el índice HNSW ya no aceptarían de todos modos, pero mejor
   fallar temprano y explícito).
 - Respeta `is_embeddable`/`is_local_embedding_provider` (D-02/RF-SEG-08):
   material Reservado/Clasificado solo se re-embebe si el proveedor
   CONFIGURADO es local/soberano (`stub`/`local_st`). Si no lo es, la fila
   queda BLOQUEADA (sigue con `embedding IS NULL`, nunca se le envía el texto
   al proveedor) y se deja traza best-effort (`knowledge.reembed_skipped_
   classified`) — mismo espíritu que `ingestion.py::ingest_radicado_created`.
 - Nunca toca `voided` ni filas con `texto = ''` (tombstones reales) — ver
   `KnowledgeRepository.list_pending_reembed_ids`.
 - Snapshot de ids POR TENANT (no cursor sobre `embedding IS NULL` en vivo):
   una fila bloqueada por residencia seguiría cumpliendo esa condición en la
   siguiente vuelta — iterar sobre una lista fija converge siempre, la
   bloquee o no `is_embeddable` (ver docstring del repositorio).
 - Exit-code != 0 si alguna fila falló (mismo contrato que `purge_revoked_
   signing_keys.py`).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import asyncpg

from app.core.clearance import is_embeddable
from app.core.config import settings
from app.repositories.knowledge_repository import KnowledgeRepository
from app.services.embeddings import EmbeddingProvider, get_provider, to_pgvector

try:
    from orpycamcp_common import audit as _audit

    _AUDIT_AVAILABLE = True
except Exception:  # noqa: BLE001
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "knowledge-service"
# Perezoso, no `os.environ[...]` a nivel de módulo: importar este script no debe
# exigir `DATABASE_URL`. Los tests de integración lo importan para ejercitar su
# lógica, y en el carril UNITARIO esa variable no existe — la colección entera
# moría con un `KeyError: 'DATABASE_URL'` pelado. Al USARLO sí se exige, con un
# mensaje que dice qué falta.
_RAW_DSN = os.environ.get("DATABASE_URL", "")
_DSN = _RAW_DSN.replace("postgresql+asyncpg://", "postgresql://") if _RAW_DSN else ""


def _require_dsn() -> str:
    if not _DSN:
        raise RuntimeError(
            "DATABASE_URL no está definida: este script de operaciones necesita la "
            "cadena de conexión a PostgreSQL."
        )
    return _DSN
_DEFAULT_BATCH_SIZE = 64


async def _tenant_schemas(conn: asyncpg.Connection) -> list[str]:
    rows = await conn.fetch(
        "SELECT schema_name FROM information_schema.schemata "
        "WHERE schema_name LIKE 'tenant\\_%' ESCAPE '\\' ORDER BY schema_name"
    )
    return [r["schema_name"] for r in rows]


async def _reembed_tenant(
    conn: asyncpg.Connection,
    *,
    schema: str,
    slug: str,
    batch_size: int,
    dry_run: bool,
    provider: EmbeddingProvider | None,
) -> tuple[int, int, int]:
    """Re-embebe (o cuenta, en dry-run) los chunks pendientes de un tenant.

    Devuelve ``(hechas_o_elegibles, bloqueadas, fallidas)``: en dry-run el
    primer elemento son las filas que SÍ se re-embeberían (pasan
    `is_embeddable`), sin tocar la BD ni el proveedor.
    """
    await conn.execute(f'SET search_path = "{schema}", public')
    repo = KnowledgeRepository(conn)
    ids = await repo.list_pending_reembed_ids()
    if not ids:
        return (0, 0, 0)

    elegibles = 0
    bloqueadas = 0
    hechas = 0
    fallidas = 0

    for i in range(0, len(ids), batch_size):
        batch_ids = ids[i : i + batch_size]
        rows = await repo.fetch_chunks_by_ids(batch_ids)
        if not rows:
            continue  # ya no pendientes (carrera con void/reclassify) -> idempotente

        embeddable_rows = []
        blocked_rows = []
        for row in rows:
            if is_embeddable(row["nivel_seguridad"], settings.embedding_provider):
                embeddable_rows.append(row)
            else:
                blocked_rows.append(row)

        elegibles += len(embeddable_rows)
        bloqueadas += len(blocked_rows)

        if blocked_rows and not dry_run and _AUDIT_AVAILABLE:
            for row in blocked_rows:
                try:
                    await _audit.append(
                        conn,
                        tenant_slug=slug,
                        service=_SERVICE,
                        action="knowledge.reembed_skipped_classified",
                        object_type=row["source_type"],
                        object_ref=row["source_ref"],
                        actor="system:reembed",
                        canal="system",
                        payload={
                            "nivel_seguridad": row["nivel_seguridad"],
                            "embedding_provider": settings.embedding_provider,
                            "motivo": "material_reservado_o_clasificado_proveedor_no_local",
                        },
                    )
                except Exception:  # noqa: BLE001 — best-effort, no bloquea el backfill
                    print(f"  [{slug}] fallo dejando traza de SKIP (best-effort)", file=sys.stderr)

        if dry_run or not embeddable_rows:
            continue

        assert provider is not None
        try:
            vectors = provider.embed_documents([row["texto"] for row in embeddable_rows])
        except Exception as exc:  # noqa: BLE001 — aísla el fallo por-lote
            fallidas += len(embeddable_rows)
            print(f"  [{slug}] FALLO embebiendo lote de {len(embeddable_rows)}: {exc}", file=sys.stderr)
            continue

        for row, vec in zip(embeddable_rows, vectors, strict=True):
            try:
                await repo.update_embedding(row["id"], to_pgvector(vec))
                hechas += 1
            except Exception as exc:  # noqa: BLE001 — aísla el fallo por-fila
                fallidas += 1
                print(f"  [{slug}] FALLO actualizando {row['id']}: {exc}", file=sys.stderr)

    if dry_run:
        print(
            f"  [{slug}] {len(ids)} pendiente(s): {elegibles} se re-embeberían, "
            f"{bloqueadas} bloqueada(s) por residencia"
        )
        return (elegibles, bloqueadas, 0)

    print(f"  [{slug}] re-embebidas: {hechas}; bloqueadas (residencia): {bloqueadas}; fallidas: {fallidas}")
    return (hechas, bloqueadas, fallidas)


async def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Re-embedding/backfill del índice de conocimiento (E21 Inc.1, migración 007)"
    )
    parser.add_argument("--tenant", help="slug de un solo tenant (default: todos)")
    parser.add_argument("--dry-run", action="store_true", help="solo cuenta lo pendiente/bloqueado")
    parser.add_argument(
        "--batch-size", type=int, default=_DEFAULT_BATCH_SIZE, help="tamaño de lote de embed_documents"
    )
    args = parser.parse_args(argv)

    conn = await asyncpg.connect(dsn=_require_dsn())
    try:
        provider: EmbeddingProvider | None = None
        if args.dry_run:
            print(f"Re-embedding (dry-run) — proveedor configurado: {settings.embedding_provider!r}")
        else:
            # Mismo guard fail-closed que app/main.py::lifespan: aborta ANTES de
            # tocar ninguna fila si el proveedor configurado no produce la
            # dimensión de la columna (EMBEDDING_DIM, migración 007).
            provider = get_provider()
            print(
                f"Re-embedding con proveedor={settings.embedding_provider!r} "
                f"modelo={provider.model_id!r} dim={provider.dim}"
            )
            if provider.dim != settings.embedding_dim:
                raise RuntimeError(
                    f"embedding_provider={settings.embedding_provider!r} produce "
                    f"dim={provider.dim}, pero EMBEDDING_DIM={settings.embedding_dim} "
                    "— aborto sin tocar ninguna fila (ajusta EMBEDDING_DIM o el "
                    "proveedor antes de reintentar)."
                )

        total_hechas = 0
        total_bloqueadas = 0
        total_fallidas = 0
        for schema in await _tenant_schemas(conn):
            # Inverso EXACTO de tenant_schema (`_`->`-`), mismo criterio que
            # purge_revoked_signing_keys.py/rewrap_signing_keys.py.
            slug = schema[len("tenant_") :].replace("_", "-") if schema.startswith("tenant_") else schema
            if args.tenant and slug != args.tenant:
                continue
            hechas, bloqueadas, fallidas = await _reembed_tenant(
                conn,
                schema=schema,
                slug=slug,
                batch_size=args.batch_size,
                dry_run=args.dry_run,
                provider=provider,
            )
            total_hechas += hechas
            total_bloqueadas += bloqueadas
            total_fallidas += fallidas
        await conn.execute("SET search_path = public")
        verb = "se re-embeberían" if args.dry_run else "re-embebidas"
        print(f"Total: {total_hechas} {verb}, {total_bloqueadas} bloqueada(s), {total_fallidas} fallida(s)")
        return 1 if total_fallidas else 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
