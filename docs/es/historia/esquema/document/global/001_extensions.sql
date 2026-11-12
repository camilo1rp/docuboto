-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración global (schema public): extensiones compartidas por todos los tenants.
--
-- Las extensiones se instalan una sola vez en `public` y se resuelven desde
-- cualquier schema tenant_{slug} porque `public` siempre está en el search_path
-- (ADR-002/012). No deben crearse por-tenant: provocaría copias redundantes.
--
--   pg_trgm   → índices GIN trigram para búsqueda difusa/prefijo (search-service)
--   unaccent  → normalización de acentos para FTS en español

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA public;
