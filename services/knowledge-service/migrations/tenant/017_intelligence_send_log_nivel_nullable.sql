-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Un push anota el nivel vivo. Una purga de una fila que ya no existe no
-- tiene nivel que anotar: NULL, no un 1 inventado. Si la fila sigue (anulado
-- o reclasificado), el nivel se escribe igual que antes.
--
-- Rollback:
--   DELETE FROM intelligence_send_log WHERE level IS NULL;
--   ALTER TABLE intelligence_send_log DROP CONSTRAINT intelligence_send_log_level_check;
--   ALTER TABLE intelligence_send_log ALTER COLUMN level SET NOT NULL;
--   ALTER TABLE intelligence_send_log ADD CONSTRAINT intelligence_send_log_level_check
--       CHECK (level BETWEEN 1 AND 3);

ALTER TABLE intelligence_send_log ALTER COLUMN level DROP NOT NULL;

ALTER TABLE intelligence_send_log DROP CONSTRAINT IF EXISTS intelligence_send_log_level_check;

ALTER TABLE intelligence_send_log
    ADD CONSTRAINT intelligence_send_log_level_check
    CHECK (level IS NULL OR level BETWEEN 1 AND 3);
