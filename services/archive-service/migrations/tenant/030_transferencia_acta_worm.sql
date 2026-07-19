-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E10 Inc.2 wiring — cableado WORM/Object-Lock del ACTA
-- de transferencia firmada, ver app/services/transferencia_service.py::
-- proteger_worm_acta y app/jobs/indice_reconciliation.py::run_once_actas_worm):
-- columnas de estado y backoff SOBRE `transferencia_acta_firma` (migración
-- 026). Espejo EXACTO de la migración 029 (`expediente_indice`), fast-follow
-- declarado en el diseño de Inc.1.
--
-- La protección WORM es ORTOGONAL a `estado` ('pendiente_firma'/'firmado',
-- migración 026 -- ese es el eje del SELLADO XAdES, no de la protección
-- WORM): un acta puede quedar 'firmado' sin que su copia inmutable en el
-- bucket WORM se haya confirmado todavía (disparo best-effort, fuera de la
-- tx del sellado) -- de ahí columnas dedicadas, mismo criterio que separó
-- `worm_*` de `reconcile_*`/`estado` en `expediente_indice`.
--
-- Aditiva/idempotente: `ADD COLUMN IF NOT EXISTS` + `CREATE INDEX IF NOT
-- EXISTS`. Segura de re-ejecutar sobre un schema ya migrado.

ALTER TABLE transferencia_acta_firma
    ADD COLUMN IF NOT EXISTS worm_protegido_at TIMESTAMPTZ;

ALTER TABLE transferencia_acta_firma
    ADD COLUMN IF NOT EXISTS worm_object_id UUID;

ALTER TABLE transferencia_acta_firma
    ADD COLUMN IF NOT EXISTS worm_reconcile_intentos INTEGER NOT NULL DEFAULT 0;

ALTER TABLE transferencia_acta_firma
    ADD COLUMN IF NOT EXISTS worm_reconcile_proximo_at TIMESTAMPTZ;

ALTER TABLE transferencia_acta_firma
    ADD COLUMN IF NOT EXISTS worm_reconcile_ultimo_error TEXT;

ALTER TABLE transferencia_acta_firma
    ADD COLUMN IF NOT EXISTS worm_reconcile_agotado BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN transferencia_acta_firma.worm_protegido_at IS
    'Instante en que storage-service confirmó la copia WORM/Object-Lock del acta (E10 Inc.2 wiring); NULL = todavía no protegido.';
COMMENT ON COLUMN transferencia_acta_firma.worm_object_id IS
    'id de la fila preservacion_worm_objeto (storage-service, tipo=acta_transferencia) que registra la copia inmutable — foránea lógica, cruza servicio.';
COMMENT ON COLUMN transferencia_acta_firma.worm_reconcile_intentos IS
    'Reintentos consumidos por run_once_actas_worm (job de reconciliación) intentando confirmar la protección WORM.';
COMMENT ON COLUMN transferencia_acta_firma.worm_reconcile_proximo_at IS
    'Próximo intento elegible de run_once_actas_worm (backoff exponencial); NULL = elegible de inmediato.';
COMMENT ON COLUMN transferencia_acta_firma.worm_reconcile_ultimo_error IS
    'Motivo del último fallo al proteger bajo WORM (p. ej. trd_sin_retencion, worm_disabled, worm_no_confirmado).';
COMMENT ON COLUMN transferencia_acta_firma.worm_reconcile_agotado IS
    'TRUE cuando se agotaron los reintentos automáticos: permanece sin proteger, requiere intervención manual.';

-- Fuente de TransferenciaRepository.list_worm_pending_actas: solo actas
-- 'firmado' aún sin proteger, no agotadas, ordenadas por elegibilidad de backoff.
CREATE INDEX IF NOT EXISTS ix_transferencia_acta_firma_worm_pendiente
    ON transferencia_acta_firma (worm_reconcile_proximo_at)
    WHERE estado = 'firmado' AND worm_protegido_at IS NULL AND worm_reconcile_agotado = FALSE;

-- Rollback (manual, no automatizado por el runner):
-- DROP INDEX IF EXISTS ix_transferencia_acta_firma_worm_pendiente;
-- ALTER TABLE transferencia_acta_firma DROP COLUMN IF EXISTS worm_reconcile_agotado;
-- ALTER TABLE transferencia_acta_firma DROP COLUMN IF EXISTS worm_reconcile_ultimo_error;
-- ALTER TABLE transferencia_acta_firma DROP COLUMN IF EXISTS worm_reconcile_proximo_at;
-- ALTER TABLE transferencia_acta_firma DROP COLUMN IF EXISTS worm_reconcile_intentos;
-- ALTER TABLE transferencia_acta_firma DROP COLUMN IF EXISTS worm_object_id;
-- ALTER TABLE transferencia_acta_firma DROP COLUMN IF EXISTS worm_protegido_at;
