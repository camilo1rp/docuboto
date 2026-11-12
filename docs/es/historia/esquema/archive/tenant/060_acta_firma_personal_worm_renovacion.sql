-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 060 (E10 debt, AREA-07-10): renovación de la retención WORM CT de las
-- firmas PERSONALES del acta. search_path = tenant_{slug} (ADR-012).
--
-- Espejo de la 032 (acta institucional) sobre `transferencia_acta_firma_personal_worm` (057).
-- `worm_retain_until` ya existe allí (lo puebla la protección inicial); solo faltan el estado y
-- el backoff del eje de RENOVACIÓN, independiente del de la primera protección.
--
-- Aditiva/idempotente: ADD COLUMN IF NOT EXISTS + CREATE INDEX IF NOT EXISTS.

ALTER TABLE transferencia_acta_firma_personal_worm
    ADD COLUMN IF NOT EXISTS worm_renovacion_intentos INTEGER NOT NULL DEFAULT 0;
ALTER TABLE transferencia_acta_firma_personal_worm
    ADD COLUMN IF NOT EXISTS worm_renovacion_proximo_at TIMESTAMPTZ;
ALTER TABLE transferencia_acta_firma_personal_worm
    ADD COLUMN IF NOT EXISTS worm_renovacion_ultimo_error TEXT;
ALTER TABLE transferencia_acta_firma_personal_worm
    ADD COLUMN IF NOT EXISTS worm_renovacion_agotado BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN transferencia_acta_firma_personal_worm.worm_renovacion_intentos IS
    'Reintentos consumidos por run_once_worm_renovacion extendiendo retain_until del XML de la firma personal (series CT).';
COMMENT ON COLUMN transferencia_acta_firma_personal_worm.worm_renovacion_agotado IS
    'TRUE cuando se agotaron los reintentos automáticos: la firma personal de una serie CT queda sin extender, requiere intervención manual URGENTE.';

CREATE INDEX IF NOT EXISTS ix_transferencia_acta_firma_personal_worm_renovacion_pendiente
    ON transferencia_acta_firma_personal_worm (worm_renovacion_proximo_at)
    WHERE worm_protegido_at IS NOT NULL AND worm_renovacion_agotado = FALSE;

-- Rollback manual (no automatizado):
-- DROP INDEX IF EXISTS ix_transferencia_acta_firma_personal_worm_renovacion_pendiente;
-- ALTER TABLE transferencia_acta_firma_personal_worm DROP COLUMN IF EXISTS worm_renovacion_agotado;
-- ALTER TABLE transferencia_acta_firma_personal_worm DROP COLUMN IF EXISTS worm_renovacion_ultimo_error;
-- ALTER TABLE transferencia_acta_firma_personal_worm DROP COLUMN IF EXISTS worm_renovacion_proximo_at;
-- ALTER TABLE transferencia_acta_firma_personal_worm DROP COLUMN IF EXISTS worm_renovacion_intentos;
