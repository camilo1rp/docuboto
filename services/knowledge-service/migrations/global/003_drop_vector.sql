-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración global (ADR-046, fase D0): se retira la extensión `vector`. Ningún
-- servicio usa pgvector; la búsqueda del respaldo `local` es el `search_vector`
-- (tsvector) de document-service. CASCADE porque en una base de desarrollo previa
-- las columnas `embedding` de `knowledge_chunk` aún dependen del tipo; esa tabla
-- la elimina `tenant/015_drop_model_layer.sql`. En una base nueva no hace nada.
DROP EXTENSION IF EXISTS vector CASCADE;
