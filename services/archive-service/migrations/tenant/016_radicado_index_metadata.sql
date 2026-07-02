-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E15 Inc.3, Acuerdo AGN 001/2024 art. 4.3.2.3): metadatos
-- RT-15 del índice electrónico — formato (MIME), tamaño en bytes y foliación.
--
-- Decisión del usuario (Q1): los tres campos se APORTAN al vincular el radicado
-- al expediente (Opción A) — igual que `content_hash` en 009 — sin que
-- archive-service consulte a document-service/storage-service. Todos son
-- NULLABLE y opcionales: un cliente que no los envía sigue funcionando igual
-- (retrocompatibilidad total del contrato de vínculo).
--
-- `expediente_indice_item.formato`/`tamano_bytes` ya existen desde
-- 008_expediente_indice.sql (declarados pero nunca poblados); aquí solo se
-- añade `folios` a esa tabla. `expediente_radicados` no tenía ninguno de los
-- tres — se añaden aquí.

ALTER TABLE expediente_radicados
    ADD COLUMN IF NOT EXISTS formato TEXT;

ALTER TABLE expediente_radicados
    ADD COLUMN IF NOT EXISTS tamano_bytes BIGINT;

ALTER TABLE expediente_radicados
    ADD COLUMN IF NOT EXISTS folios INTEGER;

ALTER TABLE expediente_indice_item
    ADD COLUMN IF NOT EXISTS folios INTEGER;

COMMENT ON COLUMN expediente_radicados.formato IS
    'Tipo MIME del documento aportado al vincular (E15 RT-15, Opción A); NULL = no declarado.';
COMMENT ON COLUMN expediente_radicados.tamano_bytes IS
    'Tamaño en bytes del documento aportado al vincular (E15 RT-15, Opción A); NULL = no declarado.';
COMMENT ON COLUMN expediente_radicados.folios IS
    'Número de folios del documento aportado al vincular (E15 RT-15, Opción A); NULL = no declarado.';
COMMENT ON COLUMN expediente_indice_item.folios IS
    'Snapshot inmutable de folios en la versión del índice (E15 RT-15); NULL = no declarado.';

-- ADD CONSTRAINT no es idempotente en PG15 (no admite IF NOT EXISTS) — se
-- localiza por definición antes de crear, igual que el patrón de 015.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        WHERE rel.relname = 'expediente_radicados'
          AND con.contype = 'c'
          AND con.conname = 'expediente_radicados_tamano_bytes_check'
    ) THEN
        ALTER TABLE expediente_radicados
            ADD CONSTRAINT expediente_radicados_tamano_bytes_check
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
        WHERE rel.relname = 'expediente_radicados'
          AND con.contype = 'c'
          AND con.conname = 'expediente_radicados_folios_check'
    ) THEN
        ALTER TABLE expediente_radicados
            ADD CONSTRAINT expediente_radicados_folios_check
            CHECK (folios IS NULL OR folios >= 0);
    END IF;
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- Rollback (referencia manual, no ejecutado por el runner):
-- ALTER TABLE expediente_radicados DROP CONSTRAINT IF EXISTS expediente_radicados_folios_check;
-- ALTER TABLE expediente_radicados DROP CONSTRAINT IF EXISTS expediente_radicados_tamano_bytes_check;
-- ALTER TABLE expediente_radicados DROP COLUMN IF EXISTS folios;
-- ALTER TABLE expediente_radicados DROP COLUMN IF EXISTS tamano_bytes;
-- ALTER TABLE expediente_radicados DROP COLUMN IF EXISTS formato;
-- ALTER TABLE expediente_indice_item DROP COLUMN IF EXISTS folios;
