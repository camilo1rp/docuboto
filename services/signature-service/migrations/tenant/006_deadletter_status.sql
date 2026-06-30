-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (ADR-021 Fase 5): ciclo de vida de evento_dead_letter.
--
-- Añade el estado del dead-letter para habilitar inspección y replay/discard:
--   pending   → recién aparcado, sin resolver (estado inicial por defecto)
--   resolved  → reprocesado con éxito por un admin (replay)
--   discarded → descartado deliberadamente por un admin (con motivo)
--
-- Idempotente: PG15 no soporta ADD COLUMN IF NOT EXISTS, por lo que se usa el
-- patrón DO $$ … information_schema.columns … $$ para no fallar en re-ejecución.
--
-- Rollback:
--   ALTER TABLE evento_dead_letter DROP COLUMN IF EXISTS status;
--   ALTER TABLE evento_dead_letter DROP COLUMN IF EXISTS resolved_at;
--   DROP INDEX IF EXISTS ix_edl_pending;
--   DROP INDEX IF EXISTS ix_edl_resolved;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'evento_dead_letter' AND column_name = 'status'
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
        WHERE table_name = 'evento_dead_letter' AND column_name = 'resolved_at'
    ) THEN
        ALTER TABLE evento_dead_letter
            ADD COLUMN resolved_at TIMESTAMPTZ NULL;
    END IF;
END $$;

-- Índice parcial para la bandeja de pendientes (consulta más frecuente).
CREATE INDEX IF NOT EXISTS ix_edl_pending
    ON evento_dead_letter (dead_lettered_at)
    WHERE status = 'pending';

-- Índice parcial para auditoría/retención de cerrados (resolved/discarded).
CREATE INDEX IF NOT EXISTS ix_edl_resolved
    ON evento_dead_letter (resolved_at)
    WHERE status IN ('resolved', 'discarded');

COMMENT ON COLUMN evento_dead_letter.status IS
    'Ciclo de vida del dead-letter: pending (inicial) → resolved (replay OK) | '
    'discarded (descarte deliberado con motivo). ADR-021 Fase 5.';
COMMENT ON COLUMN evento_dead_letter.resolved_at IS
    'Momento de la transición a resolved/discarded (NULL mientras pending).';
