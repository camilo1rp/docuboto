-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E21): habilita el patrón TOMBSTONE (KnowledgeRepository.
-- tombstone_by_source) que cierra la fuga de reclasificación reportada por
-- tenant-security-auditor: un DELETE en la ruta de reclasificación anulaba el
-- candado GREATEST de `upsert_chunk` (que solo protege ON CONFLICT, y exige que
-- la fila EXISTA), permitiendo que un `document.radicado.created` REDELIVERED
-- tarde reencarnara el chunk a su nivel viejo tras un `reclassified` que lo
-- había subido.
--
-- Decisión de diseño — NULL en `embedding` marca el tombstone (en vez de una
-- columna `is_tombstone` separada):
--   1. `KnowledgeRepository.search` ya filtra con WHERE; "AND embedding IS NOT
--      NULL" reutiliza esa cláusula existente sin columna/índice nuevos.
--   2. El índice ivfflat (`ix_knowledge_embedding`) nunca compara contra NULL:
--      un tombstone es estructuralmente inelegible como vecino, no depende de
--      que código futuro recuerde consultar un flag adicional.
--   3. `texto` y `metadata` ya son NOT NULL con DEFAULT ('' / '{}'::jsonb no
--      hace falta relajarlos) — el tombstone los vacía explícitamente
--      (`tombstone_by_source`) sin tocar su nullability.
--
-- Por eso esta migración SOLO relaja `embedding` a NULLable. Es idempotente sin
-- necesidad de envolver en un bloque DO: `ALTER COLUMN ... DROP NOT NULL` no
-- falla si la columna ya es nullable (re-ejecución segura).
--
-- Rollback (solo si NO hay filas tombstone, i.e. embedding IS NULL):
--   ALTER TABLE knowledge_chunk ALTER COLUMN embedding SET NOT NULL;

ALTER TABLE knowledge_chunk
    ALTER COLUMN embedding DROP NOT NULL;
