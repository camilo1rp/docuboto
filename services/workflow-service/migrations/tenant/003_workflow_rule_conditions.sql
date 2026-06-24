-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E05): motor de reglas con operadores y AND/OR.
--
-- `conditions` es una lista JSONB de {field, op, value}; `match_mode` combina
-- 'all' (AND) o 'any' (OR). Si `conditions` está vacío, la regla se evalúa con
-- las 4 columnas legadas (doc_type/doc_class/dest_dept/subject_contains).

ALTER TABLE workflow_rules
    ADD COLUMN IF NOT EXISTS conditions JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE workflow_rules
    ADD COLUMN IF NOT EXISTS match_mode VARCHAR(3) NOT NULL DEFAULT 'all';
