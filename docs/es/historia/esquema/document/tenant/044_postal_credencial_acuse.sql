-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


-- Tenant migration 044 (AREA-09-10): secreto HMAC del operador postal con rotación,
-- y acuse de recibo del envío.
--
-- 1) `postal_operator_credential.webhook_secret` era TEXT en claro, una sola fila por
--    operador y NINGÚN endpoint lo escribía (se sembraba a mano). Ahora hay dos
--    ranuras —la vigente y la anterior, con `anterior_caduca_at`— para rotar sin corte:
--    el operador cambia de secreto cuando puede y, mientras tanto, se aceptan ambas.
--    Las ranuras nuevas se guardan CIFRADAS (AESGCM, AAD = tenant + operador); la
--    columna en claro queda solo para lo heredado y se vacía al primer rotado.
-- 2) `postal_shipment.acuse_*`: la prueba de entrega. Un envío no puede pasar a
--    `entregado` a mano sin ella (el callback firmado del operador es su propia prueba).
--
-- Idempotente. Rollback: DROP de las columnas añadidas; restaurar NOT NULL si todas las
-- filas tienen `webhook_secret`.

ALTER TABLE postal_operator_credential ALTER COLUMN webhook_secret DROP NOT NULL;

ALTER TABLE postal_operator_credential
    ADD COLUMN IF NOT EXISTS secreto_cifrado    BYTEA,
    ADD COLUMN IF NOT EXISTS secreto_nonce      BYTEA,
    ADD COLUMN IF NOT EXISTS anterior_cifrado   BYTEA,
    ADD COLUMN IF NOT EXISTS anterior_nonce     BYTEA,
    ADD COLUMN IF NOT EXISTS anterior_caduca_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS rotado_por         UUID;

ALTER TABLE postal_shipment
    ADD COLUMN IF NOT EXISTS acuse_file_id UUID,
    ADD COLUMN IF NOT EXISTS acuse_sha256  CHAR(64);

COMMENT ON COLUMN postal_shipment.acuse_file_id IS
    'file_id (storage) del acuse de recibo: constancia de entrega que aporta quien registra la entrega a mano.';
