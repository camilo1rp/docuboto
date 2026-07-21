-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E21): idempotencia de la ingesta event-driven.
--
-- El worker de ingesta (app/workers/document_consumer.py) procesa eventos de Redis
-- Streams con semántica at-least-once: un `document.radicado.created` puede llegar
-- más de una vez (redelivery, reintento tras fallo transitorio). Sin una restricción
-- de unicidad sobre (source_type, source_ref), cada redelivery insertaría un chunk
-- duplicado en vez de actualizar el existente (INSERT ... ON CONFLICT DO UPDATE,
-- ver KnowledgeRepository.upsert_chunk).
--
-- Dedup defensivo previo: si el servicio ya acumuló filas duplicadas por
-- (source_type, source_ref) antes de este MVP (p. ej. llamadas manuales a
-- /api/v1/knowledge/ingest sin upsert), el ADD CONSTRAINT fallaría. Se conserva la
-- fila más reciente por (created_at, id) y se descartan las demás ANTES de crear la
-- restricción.
--
-- Rollback:
--   ALTER TABLE knowledge_chunk DROP CONSTRAINT IF EXISTS ux_knowledge_source;
--   ALTER TABLE knowledge_chunk DROP COLUMN IF EXISTS updated_at;
--
-- BUGFIX (testing-engineer, cobertura E21 Inc.1): AMBOS checks de este bloque
-- eran globales a la base, no acotados al schema activo — mismo defecto que
-- 002_nivel_seguridad_failclosed.sql (ver ese comentario para el razonamiento
-- completo):
--   - `pg_constraint WHERE conname = 'ux_knowledge_source'` — `conname` NO es
--     único entre schemas; en cuanto el PRIMER tenant tiene esa constraint (y
--     todos la crean con el mismo nombre), el check la encuentra ahí y se
--     salta el `ADD CONSTRAINT` para cualquier tenant provisionado después.
--     Se acota uniendo `pg_constraint` -> `pg_class` -> `pg_namespace` y
--     filtrando por `current_schema()`.
--   - `information_schema.columns WHERE table_name = 'knowledge_chunk' AND
--     column_name = 'updated_at'` — mismo defecto que en 002 (no filtra por
--     `table_schema`).
-- Ambos ahora se acotan explícitamente al schema activo (`current_schema()`,
-- el primero del `search_path` fijado antes de aplicar la migración).

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE c.conname = 'ux_knowledge_source'
           AND n.nspname = current_schema()
    ) THEN
        -- Dedup defensivo: conserva solo la fila más reciente de cada
        -- (source_type, source_ref) antes de poder crear el índice único.
        DELETE FROM knowledge_chunk kc
        USING knowledge_chunk kc2
        WHERE kc.source_type = kc2.source_type
          AND kc.source_ref = kc2.source_ref
          AND (kc.created_at, kc.id) < (kc2.created_at, kc2.id);

        ALTER TABLE knowledge_chunk
            ADD CONSTRAINT ux_knowledge_source UNIQUE (source_type, source_ref);
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = current_schema()
           AND table_name   = 'knowledge_chunk'
           AND column_name  = 'updated_at'
    ) THEN
        ALTER TABLE knowledge_chunk
            ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
    END IF;
END
$$;
