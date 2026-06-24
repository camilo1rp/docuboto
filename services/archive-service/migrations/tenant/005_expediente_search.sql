-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E09, F2, ADR-014): búsqueda full-text de expedientes.
--
-- Columna generada `search_vector` (tsvector, built-in — sin pg_trgm/unaccent para
-- no depender de extensiones) sobre code/name/description con pesos, + índice GIN.
-- El filtro por metadato reutiliza el índice GIN ya existente sobre `metadata`.

ALTER TABLE expedientes
    ADD COLUMN IF NOT EXISTS search_vector tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('spanish', coalesce(code, '')), 'A') ||
        setweight(to_tsvector('spanish', coalesce(name, '')), 'B') ||
        setweight(to_tsvector('spanish', coalesce(description, '')), 'C')
    ) STORED;

CREATE INDEX IF NOT EXISTS ix_expedientes_search
    ON expedientes USING GIN (search_vector);
