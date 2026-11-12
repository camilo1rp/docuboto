-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 056 (E12/E10, AREA-07-05): el fixity-check de la RECEPCIÓN como evento PREMIS.
-- search_path = tenant_{slug} (ADR-012).
--
-- `recibir()` verifica la fixity del acervo dentro de la transacción de recepción y la dejaba
-- solo en el payload del asiento `archive.transferencia_recibida`: no aparecía en
-- `GET /expedientes/{id}/preservacion/eventos`, que es donde un archivista busca la historia
-- de preservación de un expediente. Ahora se registra además como evento PREMIS en
-- storage-service (best-effort, POST-commit: el almacén de eventos es otro servicio).
--
-- «Best-effort con reconciliación»: si el registro falla (storage caído), la recepción NO se
-- revierte —el acta ya es íntegra y su asiento está anclado— y la reconciliación lo repite con
-- backoff, como el WORM del acta. Para poder repetirlo el detalle de la fixity (que solo
-- existía en memoria durante la recepción) se guarda aquí, en la fila de bookkeeping de la
-- firma del acta, que es la única de las tablas del acta que admite UPDATE (la `transferencia_acta`
-- es append-only: trigger 023 + REVOKE 054).
--
-- Las filas anteriores a esta migración tienen `fixity_premis_detalle` NULL: no hay detalle con
-- el que reconstruir su evento y NO se inventa uno (solo quedan en el asiento, como hasta hoy).
--
-- Aditiva/idempotente: ADD COLUMN IF NOT EXISTS + CREATE INDEX IF NOT EXISTS.

ALTER TABLE transferencia_acta_firma
    ADD COLUMN IF NOT EXISTS fixity_premis_detalle JSONB;
ALTER TABLE transferencia_acta_firma
    ADD COLUMN IF NOT EXISTS fixity_premis_at TIMESTAMPTZ;
ALTER TABLE transferencia_acta_firma
    ADD COLUMN IF NOT EXISTS fixity_premis_intentos INTEGER NOT NULL DEFAULT 0;
ALTER TABLE transferencia_acta_firma
    ADD COLUMN IF NOT EXISTS fixity_premis_proximo_at TIMESTAMPTZ;
ALTER TABLE transferencia_acta_firma
    ADD COLUMN IF NOT EXISTS fixity_premis_ultimo_error TEXT;
ALTER TABLE transferencia_acta_firma
    ADD COLUMN IF NOT EXISTS fixity_premis_agotado BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN transferencia_acta_firma.fixity_premis_detalle IS
    'Detalle de la fixity de la recepción (documentos_ok, huella_indice_ok, documentos_sin_huella...) tal como se verificó en la tx de recibir(); NULL en las recepciones anteriores a la migración 056.';
COMMENT ON COLUMN transferencia_acta_firma.fixity_premis_at IS
    'Instante en que storage-service confirmó el evento PREMIS del fixity-check; NULL = pendiente.';

-- Fuente de TransferenciaRepository.list_fixity_premis_pending.
CREATE INDEX IF NOT EXISTS ix_transferencia_acta_firma_fixity_premis_pendiente
    ON transferencia_acta_firma (fixity_premis_proximo_at)
    WHERE fixity_premis_detalle IS NOT NULL
      AND fixity_premis_at IS NULL
      AND fixity_premis_agotado = FALSE;

-- Rollback manual (no automatizado):
-- DROP INDEX IF EXISTS ix_transferencia_acta_firma_fixity_premis_pendiente;
-- ALTER TABLE transferencia_acta_firma DROP COLUMN IF EXISTS fixity_premis_agotado;
-- ALTER TABLE transferencia_acta_firma DROP COLUMN IF EXISTS fixity_premis_ultimo_error;
-- ALTER TABLE transferencia_acta_firma DROP COLUMN IF EXISTS fixity_premis_proximo_at;
-- ALTER TABLE transferencia_acta_firma DROP COLUMN IF EXISTS fixity_premis_intentos;
-- ALTER TABLE transferencia_acta_firma DROP COLUMN IF EXISTS fixity_premis_at;
-- ALTER TABLE transferencia_acta_firma DROP COLUMN IF EXISTS fixity_premis_detalle;
