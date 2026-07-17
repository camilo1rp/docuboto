-- Migración de tenant (residual Ac. AGN 001/2024 art. 4.3.2.4 — reconciliación
-- del sellado del índice electrónico, ver app/jobs/indice_reconciliation.py):
-- columnas de backoff sobre `expediente_indice` para las filas que quedaron
-- 'pendiente_firma' (el CHECK de `estado` NO cambia — migración 015).
--
-- Aditiva/idempotente: `ADD COLUMN IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS`.
-- Segura de re-ejecutar sobre un schema ya migrado.

ALTER TABLE expediente_indice
    ADD COLUMN IF NOT EXISTS reconcile_intentos INT NOT NULL DEFAULT 0;

ALTER TABLE expediente_indice
    ADD COLUMN IF NOT EXISTS reconcile_proximo_at TIMESTAMPTZ;

ALTER TABLE expediente_indice
    ADD COLUMN IF NOT EXISTS reconcile_ultimo_error TEXT;

ALTER TABLE expediente_indice
    ADD COLUMN IF NOT EXISTS reconcile_agotado BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN expediente_indice.reconcile_intentos IS
    'Reintentos consumidos por el job de reconciliación del sellado (residual Ac. AGN 001/2024 art. 4.3.2.4).';
COMMENT ON COLUMN expediente_indice.reconcile_proximo_at IS
    'Próximo intento elegible del job (backoff exponencial); NULL = elegible de inmediato.';
COMMENT ON COLUMN expediente_indice.reconcile_ultimo_error IS
    'Motivo del último fallo de sellado — visible en GET /expedientes/indices/pendientes-firma.';
COMMENT ON COLUMN expediente_indice.reconcile_agotado IS
    'TRUE cuando se agotó indice_reconciliation_max_intentos: permanece pendiente_firma, requiere intervención manual (POST /indice/firmar).';

-- Fuente de `IndexRepository.list_pending_signature`: solo índices
-- 'pendiente_firma' no agotados, ordenados por elegibilidad de backoff.
CREATE INDEX IF NOT EXISTS ix_expediente_indice_reconcile_pendiente
    ON expediente_indice (reconcile_proximo_at)
    WHERE estado = 'pendiente_firma' AND reconcile_agotado = FALSE;

-- Rollback (manual, no automatizado por el runner):
-- DROP INDEX IF EXISTS ix_expediente_indice_reconcile_pendiente;
-- ALTER TABLE expediente_indice DROP COLUMN IF EXISTS reconcile_agotado;
-- ALTER TABLE expediente_indice DROP COLUMN IF EXISTS reconcile_ultimo_error;
-- ALTER TABLE expediente_indice DROP COLUMN IF EXISTS reconcile_proximo_at;
-- ALTER TABLE expediente_indice DROP COLUMN IF EXISTS reconcile_intentos;
