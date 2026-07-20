-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E10 debt — renovación de retención WORM para series
-- CT — ver app/services/transferencia_service.py::renovar_worm_acta y
-- app/jobs/indice_reconciliation.py::run_once_worm_renovacion): espejo
-- LOCAL de `preservacion_worm_objeto.retain_until` (storage-service,
-- migración 011) + estado/backoff de la 5ª fase de reconciliación SOBRE
-- `transferencia_acta_firma` (migración 026). Espejo EXACTO de la migración
-- 031 (`expediente_indice`), mismo criterio que ató la migración 030 a la 029.
--
-- ORTOGONAL a `worm_protegido_at`/`worm_reconcile_*` (migración 030, eje de
-- LA PRIMERA protección) y a `estado`/`reconcile_*` (sellado XAdES) — un eje
-- de backoff INDEPENDIENTE, tercero sobre esta tabla.
--
-- Aditiva/idempotente: `ADD COLUMN IF NOT EXISTS` + `CREATE INDEX IF NOT
-- EXISTS`. Segura de re-ejecutar sobre un schema ya migrado.

ALTER TABLE transferencia_acta_firma
    ADD COLUMN IF NOT EXISTS worm_retain_until TIMESTAMPTZ;

ALTER TABLE transferencia_acta_firma
    ADD COLUMN IF NOT EXISTS worm_renovacion_intentos INTEGER NOT NULL DEFAULT 0;

ALTER TABLE transferencia_acta_firma
    ADD COLUMN IF NOT EXISTS worm_renovacion_proximo_at TIMESTAMPTZ;

-- Ver migración 031 (expediente_indice) para la justificación de esta
-- columna adicional (deviación deliberada, consistencia con los demás ejes
-- de backoff del servicio).
ALTER TABLE transferencia_acta_firma
    ADD COLUMN IF NOT EXISTS worm_renovacion_ultimo_error TEXT;

ALTER TABLE transferencia_acta_firma
    ADD COLUMN IF NOT EXISTS worm_renovacion_agotado BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN transferencia_acta_firma.worm_retain_until IS
    'Espejo LOCAL del retain_until vigente en preservacion_worm_objeto (storage-service, tipo=acta_transferencia) -- poblado al proteger (mark_acta_worm_protegido) y actualizado al renovar (mark_acta_worm_renovado). NULL mientras worm_protegido_at sea NULL.';
COMMENT ON COLUMN transferencia_acta_firma.worm_renovacion_intentos IS
    'Reintentos consumidos por run_once_worm_renovacion (5ª fase) intentando extender retain_until antes de su vencimiento, para el acta de expedientes de series CT.';
COMMENT ON COLUMN transferencia_acta_firma.worm_renovacion_proximo_at IS
    'Próximo intento elegible de run_once_worm_renovacion (backoff exponencial); NULL = elegible de inmediato.';
COMMENT ON COLUMN transferencia_acta_firma.worm_renovacion_ultimo_error IS
    'Motivo del último fallo al renovar la retención WORM del acta (p. ej. trd_sin_retencion, worm_disabled, worm_no_confirmado, worm_object_not_found).';
COMMENT ON COLUMN transferencia_acta_firma.worm_renovacion_agotado IS
    'TRUE cuando se agotaron los reintentos automáticos de renovación: el acta de una serie CT queda sin extender, requiere intervención manual URGENTE.';

CREATE INDEX IF NOT EXISTS ix_transferencia_acta_firma_worm_renovacion_pendiente
    ON transferencia_acta_firma (worm_renovacion_proximo_at)
    WHERE worm_protegido_at IS NOT NULL AND worm_renovacion_agotado = FALSE;

-- Rollback (manual, no automatizado por el runner):
-- DROP INDEX IF EXISTS ix_transferencia_acta_firma_worm_renovacion_pendiente;
-- ALTER TABLE transferencia_acta_firma DROP COLUMN IF EXISTS worm_renovacion_agotado;
-- ALTER TABLE transferencia_acta_firma DROP COLUMN IF EXISTS worm_renovacion_ultimo_error;
-- ALTER TABLE transferencia_acta_firma DROP COLUMN IF EXISTS worm_renovacion_proximo_at;
-- ALTER TABLE transferencia_acta_firma DROP COLUMN IF EXISTS worm_renovacion_intentos;
-- ALTER TABLE transferencia_acta_firma DROP COLUMN IF EXISTS worm_retain_until;
