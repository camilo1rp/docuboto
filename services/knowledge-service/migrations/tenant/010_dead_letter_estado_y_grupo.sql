-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (AREA-10-10, E21): paridad de knowledge con el resto de
-- consumidores sobre la tabla COMPARTIDA evento_dead_letter, para poder
-- inspeccionarla y reprocesarla (admin DLQ).
--
-- knowledge creaba la tabla sin `status`/`resolved_at` ni NOT NULL en origin_group
-- (workflow/signature/notification sí). Si otro servicio migró antes, todo esto es
-- no-op; si knowledge migra primero, deja la tabla con la forma que esperan los
-- demás. Idempotente.
--
-- Rollback:
--   DROP INDEX IF EXISTS ix_edl_tenant_group_status, ix_edl_pending, ix_edl_resolved;

UPDATE evento_dead_letter SET origin_group = 'unknown' WHERE origin_group IS NULL;

ALTER TABLE evento_dead_letter
    ALTER COLUMN origin_group SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'evento_dead_letter' AND column_name = 'status'
    ) THEN
        ALTER TABLE evento_dead_letter
            ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'resolved', 'discarded'));
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'evento_dead_letter' AND column_name = 'resolved_at'
    ) THEN
        ALTER TABLE evento_dead_letter ADD COLUMN resolved_at TIMESTAMPTZ NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_edl_pending
    ON evento_dead_letter (dead_lettered_at) WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS ix_edl_resolved
    ON evento_dead_letter (resolved_at) WHERE status IN ('resolved', 'discarded');

CREATE INDEX IF NOT EXISTS ix_edl_tenant_group_status
    ON evento_dead_letter (tenant_slug, origin_group, status);
