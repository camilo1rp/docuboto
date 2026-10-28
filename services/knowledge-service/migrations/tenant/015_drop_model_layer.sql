-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (ADR-046, fase D0): knowledge-service deja de llamar a modelos.
-- Sin índice propio ni sugerencias generadas en local, sobran `knowledge_chunk` (con su
-- índice HNSW) y `kb_suggestions`. No hay instalaciones con datos: no se conserva
-- histórico. `evento_dead_letter` NO se toca: la comparten los demás consumidores.
DROP TABLE IF EXISTS knowledge_chunk;
DROP TABLE IF EXISTS kb_suggestions;
