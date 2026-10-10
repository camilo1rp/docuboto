-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (AREA-10-08/E21, ADR-021): segunda red contra la inversión de
-- niveles por reordenamiento. `nivel_evento_at` guarda el `occurred_at` del último
-- evento de reclasificación aplicado al chunk; un evento más viejo (replay tardío
-- desde la DLQ) ya no baja el nivel (ver update_nivel_by_source). NULL = sin evento
-- de nivel aplicado todavía (comportamiento previo: solo sube).
-- Rollback: ALTER TABLE knowledge_chunk DROP COLUMN IF EXISTS nivel_evento_at;
ALTER TABLE knowledge_chunk ADD COLUMN IF NOT EXISTS nivel_evento_at timestamptz;
