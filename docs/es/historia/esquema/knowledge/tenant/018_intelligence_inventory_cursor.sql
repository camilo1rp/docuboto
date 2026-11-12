-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Cursor de la reconciliación contra el inventario del proveedor. NULL es el
-- principio de una pasada. El tope es el del protocolo (512).
--
-- Rollback:
--   ALTER TABLE intelligence_state DROP CONSTRAINT IF EXISTS chk_intelligence_state_inventario;
--   ALTER TABLE intelligence_state DROP COLUMN IF EXISTS inventory_cursor;

ALTER TABLE intelligence_state
    ADD COLUMN IF NOT EXISTS inventory_cursor TEXT;

ALTER TABLE intelligence_state
    DROP CONSTRAINT IF EXISTS chk_intelligence_state_inventario;

ALTER TABLE intelligence_state
    ADD CONSTRAINT chk_intelligence_state_inventario
    CHECK (
        inventory_cursor IS NULL
        OR char_length(inventory_cursor) BETWEEN 1 AND 512
    );
