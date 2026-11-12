-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E04/RF-TRD-07 + E10, 2026-08-29): contabilidad del
-- cableado WORM del ACTA DE ELIMINACIÓN.
--
-- Cierra la deuda declarada al cerrar la fase 4: el acta se quedó con sus tres
-- garantías locales (trigger append-only, SHA anclado en `audit_log` y firma
-- XAdES) y sin Object-Lock. La razón era de MODELO y ya está corregida en
-- storage-service (su migración 014 admite un propietario de tipo `lote`); esto
-- es su contabilidad de este lado.
--
-- Gemela EXACTA de las columnas `worm_*` de `transferencia_acta_firma`: mismo
-- juego de marcas y mismo backoff, para que la reconciliación sea el patrón que
-- ya existe y no uno nuevo.

ALTER TABLE eliminacion_acta_firma
    ADD COLUMN IF NOT EXISTS worm_protegido_at        TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS worm_object_id           UUID,
    ADD COLUMN IF NOT EXISTS worm_retain_until        TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS worm_last_error          TEXT,
    ADD COLUMN IF NOT EXISTS worm_reconcile_intentos  INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS worm_reconcile_proximo_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS worm_reconcile_agotado   BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN eliminacion_acta_firma.worm_retain_until IS
    'Fin de la retención Object-Lock del acta. Conservación PERMANENTE se materializa como el máximo expresable (100 años) porque Object-Lock exige una fecha, no un infinito; es renovable por la misma vía que las series CT.';

-- Solo se protege lo YA FIRMADO: el índice parcial es el que la reconciliación
-- recorrerá, y una acta sin firmar no tiene artefacto que proteger.
CREATE INDEX IF NOT EXISTS ix_eliminacion_acta_firma_worm_pendiente
    ON eliminacion_acta_firma (worm_reconcile_proximo_at)
    WHERE estado = 'firmado'
      AND worm_protegido_at IS NULL
      AND worm_reconcile_agotado = false;

-- Rollback (manual, no automatizado por el runner):
-- DROP INDEX IF EXISTS ix_eliminacion_acta_firma_worm_pendiente;
-- ALTER TABLE eliminacion_acta_firma
--     DROP COLUMN IF EXISTS worm_protegido_at, DROP COLUMN IF EXISTS worm_object_id,
--     DROP COLUMN IF EXISTS worm_retain_until, DROP COLUMN IF EXISTS worm_last_error,
--     DROP COLUMN IF EXISTS worm_reconcile_intentos,
--     DROP COLUMN IF EXISTS worm_reconcile_proximo_at,
--     DROP COLUMN IF EXISTS worm_reconcile_agotado;
