-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E06, AREA-04-11): `key_version` en `firma` y en
-- `firma_challenge`.
--
-- Hasta aquí un único `signature_secret` firmaba el HMAC de la firma nativa y
-- el HMAC del OTP, igual para todas las entidades, y el mensaje de la firma no
-- nombraba entidad ni objeto. Desde la 020 las claves salen de HKDF por
-- (propósito, entidad) y el mensaje ata entidad+tipo+objeto (la firma) o el
-- `challenge_id` (el OTP). Ver `app/core/secret_derivation.py`.
--
-- ── Por qué el DEFAULT es 0 y no 1 ──────────────────────────────────────────
--
-- 0 es el esquema ANTERIOR. Toda fila que existe hoy se produjo con él, así que
-- el DEFAULT hace que `ADD COLUMN` las deje etiquetadas correctamente sin
-- tocarlas (una firma es un hecho fechado y su fila no se reescribe): siguen
-- verificando con la clave raíz y el mensaje original. El código nuevo escribe
-- el valor ACTUAL (1) de forma explícita en cada INSERT.
--
-- ── Qué NO hace ─────────────────────────────────────────────────────────────
--
-- Solo importa para `firma.provider = 'nativa'` (las XAdES no llevan HMAC de
-- servicio; para ellas la columna vale 0 y no se lee). No re-firma nada ni
-- rota el secreto raíz.
--
-- Rollback (solo antes de que haya filas con key_version = 1, o esas dejarán de
-- verificar):
--   ALTER TABLE firma DROP COLUMN IF EXISTS key_version;
--   ALTER TABLE firma_challenge DROP COLUMN IF EXISTS key_version;

ALTER TABLE firma
    ADD COLUMN IF NOT EXISTS key_version SMALLINT NOT NULL DEFAULT 0
        CHECK (key_version IN (0, 1));

ALTER TABLE firma_challenge
    ADD COLUMN IF NOT EXISTS key_version SMALLINT NOT NULL DEFAULT 0
        CHECK (key_version IN (0, 1));

COMMENT ON COLUMN firma.key_version IS
    'Esquema del HMAC de la firma NATIVA: 0 = clave raiz directa y mensaje sin '
    'entidad ni objeto (anterior a la 020); 1 = clave HKDF por entidad y '
    'mensaje que ata entidad+tipo+objeto. Solo se lee para provider=nativa.';

COMMENT ON COLUMN firma_challenge.key_version IS
    'Esquema del HMAC del OTP: 0 = clave raiz directa, sin challenge_id; '
    '1 = clave HKDF por entidad y mensaje que ata el challenge_id.';
