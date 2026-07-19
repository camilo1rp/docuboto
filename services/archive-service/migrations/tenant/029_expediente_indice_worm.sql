-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E10 Inc.1 — cableado WORM/Object-Lock del ÍNDICE
-- electrónico firmado, ver app/services/index_service.py::proteger_worm_indice
-- y app/jobs/indice_reconciliation.py::run_once_worm): columnas de estado y
-- backoff SOBRE `expediente_indice`.
--
-- La protección WORM es ORTOGONAL a `estado` ('vigente'/'pendiente_firma'/
-- 'firmado', migraciones 008/015): un índice puede quedar 'firmado' (sellado
-- XAdES-B, E06 Inc.1) sin que su copia inmutable en el bucket WORM se haya
-- confirmado todavía (disparo best-effort, fuera de la tx del sellado) — de
-- ahí columnas dedicadas en vez de reusar `estado`/`reconcile_*` (esas son
-- del SELLADO, no de la protección WORM; un índice puede estar
-- `reconcile_agotado=TRUE` del sellado y aun así protegerse bajo WORM sin
-- problema, son ejes independientes).
--
-- Aditiva/idempotente: `ADD COLUMN IF NOT EXISTS` + `CREATE INDEX IF NOT
-- EXISTS`. Segura de re-ejecutar sobre un schema ya migrado.

ALTER TABLE expediente_indice
    ADD COLUMN IF NOT EXISTS worm_protegido_at TIMESTAMPTZ;

ALTER TABLE expediente_indice
    ADD COLUMN IF NOT EXISTS worm_object_id UUID;

ALTER TABLE expediente_indice
    ADD COLUMN IF NOT EXISTS worm_reconcile_intentos INTEGER NOT NULL DEFAULT 0;

ALTER TABLE expediente_indice
    ADD COLUMN IF NOT EXISTS worm_reconcile_proximo_at TIMESTAMPTZ;

ALTER TABLE expediente_indice
    ADD COLUMN IF NOT EXISTS worm_reconcile_ultimo_error TEXT;

ALTER TABLE expediente_indice
    ADD COLUMN IF NOT EXISTS worm_reconcile_agotado BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN expediente_indice.worm_protegido_at IS
    'Instante en que storage-service confirmó la copia WORM/Object-Lock del índice (E10 Inc.1); NULL = todavía no protegido.';
COMMENT ON COLUMN expediente_indice.worm_object_id IS
    'id de la fila preservacion_worm_objeto (storage-service) que registra la copia inmutable — foránea lógica, cruza servicio.';
COMMENT ON COLUMN expediente_indice.worm_reconcile_intentos IS
    'Reintentos consumidos por run_once_worm (job de reconciliación) intentando confirmar la protección WORM.';
COMMENT ON COLUMN expediente_indice.worm_reconcile_proximo_at IS
    'Próximo intento elegible de run_once_worm (backoff exponencial); NULL = elegible de inmediato.';
COMMENT ON COLUMN expediente_indice.worm_reconcile_ultimo_error IS
    'Motivo del último fallo al proteger bajo WORM (p. ej. trd_sin_retencion, worm_disabled, worm_no_confirmado).';
COMMENT ON COLUMN expediente_indice.worm_reconcile_agotado IS
    'TRUE cuando se agotaron los reintentos automáticos: permanece sin proteger, requiere intervención manual.';

-- Fuente de IndexRepository.list_worm_pending: solo índices 'firmado' aún sin
-- proteger, no agotados, ordenados por elegibilidad de backoff.
CREATE INDEX IF NOT EXISTS ix_expediente_indice_worm_pendiente
    ON expediente_indice (worm_reconcile_proximo_at)
    WHERE estado = 'firmado' AND worm_protegido_at IS NULL AND worm_reconcile_agotado = FALSE;

-- Rollback (manual, no automatizado por el runner):
-- DROP INDEX IF EXISTS ix_expediente_indice_worm_pendiente;
-- ALTER TABLE expediente_indice DROP COLUMN IF EXISTS worm_reconcile_agotado;
-- ALTER TABLE expediente_indice DROP COLUMN IF EXISTS worm_reconcile_ultimo_error;
-- ALTER TABLE expediente_indice DROP COLUMN IF EXISTS worm_reconcile_proximo_at;
-- ALTER TABLE expediente_indice DROP COLUMN IF EXISTS worm_reconcile_intentos;
-- ALTER TABLE expediente_indice DROP COLUMN IF EXISTS worm_object_id;
-- ALTER TABLE expediente_indice DROP COLUMN IF EXISTS worm_protegido_at;
