-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E21 Inc.1): adopta un proveedor de embeddings REAL
-- (sentence-transformers multilingüe, `local_st`, ver app/services/embeddings.py)
-- en reemplazo del stub determinista (dim 64). `EMBEDDING_DIM` es ahora la
-- fuente ÚNICA de la dimensión de esta columna (una sola columna/índice ANN
-- por schema, no una por-proveedor — ver app/main.py::lifespan, guard fail-
-- closed que aborta el arranque si el proveedor configurado no produce
-- exactamente esta dimensión).
--
-- CC-01 (capa DERIVADA, reconstruible — no es dato maestro): los embeddings de
-- dim 64 existentes son matemáticamente INCOMPATIBLES con dim 384 (no hay
-- conversión válida entre espacios vectoriales de distinto modelo). Esta
-- migración:
--   1. NULLea los `embedding` existentes — quedan como CENTINELAS al nivel
--      (mismo patrón que `tombstone_by_source`, migración 005): la columna ya
--      es NULLable desde 005, y `KnowledgeRepository.search` los excluye vía
--      `embedding IS NOT NULL`. NO se pierde el dato maestro: `texto` NO se
--      toca (permanece para el backfill, ver más abajo) y las filas tombstone/
--      voided reales (`texto = ''`) simplemente se quedan igual (ya eran NULL
--      o pasan a serlo, sin diferencia observable).
--   2. Cambia el TIPO de la columna a `vector(384)`. Con la columna ya vacía,
--      el ALTER es barato (no hay filas de 64 elementos que reconvertir).
--   3. Sustituye el índice ANN `ivfflat` (necesitaba `ANALYZE` con datos reales
--      para rendir, y arrancaba desentrenado en un índice vacío) por **HNSW**
--      (`vector_cosine_ops`, mismo operador que ya usa `KnowledgeRepository`
--      .search` en el `ORDER BY embedding <=> $1`): recall estable sin
--      entrenamiento previo, ideal para un índice que nace vacío tras este
--      backfill.
--
-- REQUIERE BACKFILL: tras aplicar esta migración, ningún chunk es recuperable
-- por `search` hasta que se re-embeban. Ejecutar (por tenant o para todos):
--
--     docker compose run --rm --no-deps knowledge-service \
--         python -m app.ops.reembed --tenant <slug>
--
-- El backfill NO vuelve a la fuente (document-service): el texto YA está
-- persistido en `knowledge_chunk.texto` (capa derivada, CC-01) — solo
-- re-embebe. Respeta `is_embeddable`/`is_local_embedding_provider` (fail-
-- closed: material Reservado/Clasificado solo se re-embebe con un proveedor
-- LOCAL) e ignora las filas tombstone/voided reales (`texto = ''`).
--
-- Idempotente sin bloque DO: un `UPDATE ... WHERE embedding IS NOT NULL` sobre
-- una tabla ya vacía de embeddings no hace nada; `ALTER COLUMN ... TYPE
-- vector(384)` sobre una columna que YA es `vector(384)` no falla; `DROP INDEX
-- IF EXISTS` / `CREATE INDEX IF NOT EXISTS` son naturalmente re-ejecutables
-- (mismo criterio que las migraciones 005/006).
--
-- Rollback (perdiendo cualquier re-embedding ya hecho tras esta migración):
--   ALTER TABLE knowledge_chunk ALTER COLUMN embedding TYPE vector(64);
--   DROP INDEX IF EXISTS ix_knowledge_embedding_hnsw;
--   CREATE INDEX ix_knowledge_embedding
--       ON knowledge_chunk USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

UPDATE knowledge_chunk
   SET embedding = NULL
 WHERE embedding IS NOT NULL;

ALTER TABLE knowledge_chunk
    ALTER COLUMN embedding TYPE public.vector(384);

DROP INDEX IF EXISTS ix_knowledge_embedding;

CREATE INDEX IF NOT EXISTS ix_knowledge_embedding_hnsw
    ON knowledge_chunk USING hnsw (embedding public.vector_cosine_ops);
