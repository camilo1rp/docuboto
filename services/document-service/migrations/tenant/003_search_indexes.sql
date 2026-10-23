-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Full-text search indexes for radicados using PostgreSQL native FTS
-- Uses pg_trgm for fuzzy matching + tsvector for ranked search.
--
-- Las extensiones pg_trgm/unaccent se crean en global/001_extensions.sql
-- (schema public, compartidas); aquí sólo se crean objetos por-tenant.

-- tsvector column for ranked full-text search
ALTER TABLE radicados
    ADD COLUMN IF NOT EXISTS search_vector tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('spanish', coalesce(tracking_number, '')), 'A') ||
        setweight(to_tsvector('spanish', coalesce(subject, '')), 'B') ||
        setweight(to_tsvector('spanish', coalesce(sender_name, '')), 'C') ||
        setweight(to_tsvector('spanish', coalesce(sender_entity, '')), 'C') ||
        setweight(to_tsvector('spanish', coalesce(dest_dept, '')), 'D') ||
        setweight(to_tsvector('spanish', coalesce(observations, '')), 'D')
    ) STORED;

-- GIN index for tsvector (fast full-text search)
CREATE INDEX IF NOT EXISTS ix_radicados_search_vector
    ON radicados USING GIN (search_vector);

-- Trigram indexes for fuzzy/prefix matching
CREATE INDEX IF NOT EXISTS ix_radicados_tracking_trgm
    ON radicados USING GIN (tracking_number public.gin_trgm_ops);

CREATE INDEX IF NOT EXISTS ix_radicados_subject_trgm
    ON radicados USING GIN (subject public.gin_trgm_ops);

CREATE INDEX IF NOT EXISTS ix_radicados_sender_trgm
    ON radicados USING GIN (sender_name public.gin_trgm_ops);
