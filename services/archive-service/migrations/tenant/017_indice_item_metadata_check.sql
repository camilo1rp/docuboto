-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E15 Inc.3, RT-15, auditoría de código): endurece el
-- snapshot inmutable del índice electrónico con los mismos CHECK que ya
-- protegen `expediente_radicados` (016_radicado_index_metadata.sql).
--
-- `expediente_indice_item.tamano_bytes` (008_expediente_indice.sql) y
-- `.folios` (016) no tenían CHECK (>=0): hoy es seguro porque los valores se
-- copian desde un vínculo ya validado al regenerar el índice, pero un camino
-- de inserción directo/erróneo no quedaría acotado en el snapshot. Defensa
-- en profundidad, sin impacto en filas existentes (siempre derivadas de
-- vínculos ya validados).

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        WHERE rel.relname = 'expediente_indice_item'
          AND con.contype = 'c'
          AND con.conname = 'expediente_indice_item_tamano_bytes_check'
    ) THEN
        ALTER TABLE expediente_indice_item
            ADD CONSTRAINT expediente_indice_item_tamano_bytes_check
            CHECK (tamano_bytes IS NULL OR tamano_bytes >= 0);
    END IF;
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        WHERE rel.relname = 'expediente_indice_item'
          AND con.contype = 'c'
          AND con.conname = 'expediente_indice_item_folios_check'
    ) THEN
        ALTER TABLE expediente_indice_item
            ADD CONSTRAINT expediente_indice_item_folios_check
            CHECK (folios IS NULL OR folios >= 0);
    END IF;
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- Rollback (referencia manual, no ejecutado por el runner):
-- ALTER TABLE expediente_indice_item DROP CONSTRAINT IF EXISTS expediente_indice_item_folios_check;
-- ALTER TABLE expediente_indice_item DROP CONSTRAINT IF EXISTS expediente_indice_item_tamano_bytes_check;
