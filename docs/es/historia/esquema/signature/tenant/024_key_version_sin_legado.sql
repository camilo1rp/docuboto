-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (B9-5): se retira el esquema HMAC `key_version = 0`.
--
-- Hasta la 020 la firma nativa y el OTP usaban la clave raíz directa y un
-- mensaje sin entidad ni objeto. La 020 los dejó en `key_version = 0` y prometió
-- que «seguían verificando»; B9 retira esa rama del código: una fila NATIVA con 0
-- ya no verifica (`/verify` responde `motivo = esquema_hmac_retirado`).
--
-- `0` pasa a significar «sin HMAC de servicio» (relleno de las filas no nativas,
-- XAdES, que no lo usan). Se quita el DEFAULT: un default que significa «esquema
-- retirado» es justo cómo nace una fila mal etiquetada en silencio. Los dos
-- INSERT (`firma_repository`, `challenge_repository`) pasan el valor explícito.
-- El CHECK (0,1) se mantiene: endurecerlo exigiría tocar filas.
--
-- Solo esquema, idempotente. Sin backfill.

ALTER TABLE firma           ALTER COLUMN key_version DROP DEFAULT;
ALTER TABLE firma_challenge ALTER COLUMN key_version DROP DEFAULT;

COMMENT ON COLUMN firma.key_version IS
    'Esquema del HMAC de la firma NATIVA: 1 = clave HKDF por entidad y mensaje que ata '
    'entidad+tipo+objeto. 0 = sin HMAC de servicio (firmas no nativas). Una fila nativa '
    'con 0 (esquema anterior a la 020, retirado en B9) no verifica.';
COMMENT ON COLUMN firma_challenge.key_version IS
    'Esquema del HMAC del OTP: 1 = clave HKDF por entidad que ata el challenge_id. '
    '0 = esquema retirado (no verifica).';
