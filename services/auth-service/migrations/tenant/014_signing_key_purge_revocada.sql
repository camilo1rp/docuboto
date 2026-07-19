-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E17/F4 -- purga del material privado de credenciales de
-- firma REVOCADAS): minimización de datos. Una privada de FIRMA revocada no
-- tiene uso operativo (el path de firma exige estado='activo'; verificación y
-- revocation-status usan solo el cert PÚBLICO + estado en BD, nunca la privada),
-- así que se ANULA su material (`private_key_cifrado`/`private_key_nonce`) tras
-- una ventana de retención, conservando TODO lo público (la fila NO se borra).
--
-- Cambios: hace NULLABLE el material privado (para poder purgarlo), añade la
-- marca `private_key_purgado_at`, y un CHECK invariante que ata el estado físico:
--   * fila CON material  -> ambas columnas NOT NULL y purgado_at NULL;
--   * fila PURGADA        -> ambas columnas NULL, purgado_at NOT NULL, y
--                            SOLO si estado='revocado' (nunca se purga una activa).
-- Las filas existentes (con material, purgado_at NULL) satisfacen la 1ra rama,
-- así que la migración no falla sobre datos. Idempotente.

ALTER TABLE auth_user_signing_key
    ALTER COLUMN private_key_cifrado DROP NOT NULL,
    ALTER COLUMN private_key_nonce  DROP NOT NULL;

ALTER TABLE auth_user_signing_key
    ADD COLUMN IF NOT EXISTS private_key_purgado_at TIMESTAMPTZ;

ALTER TABLE auth_user_signing_key
    DROP CONSTRAINT IF EXISTS chk_auth_user_signing_key_purga;

ALTER TABLE auth_user_signing_key
    ADD CONSTRAINT chk_auth_user_signing_key_purga CHECK (
        (
            private_key_cifrado    IS NOT NULL
            AND private_key_nonce  IS NOT NULL
            AND private_key_purgado_at IS NULL
        )
        OR (
            private_key_cifrado    IS NULL
            AND private_key_nonce  IS NULL
            AND private_key_purgado_at IS NOT NULL
            AND estado = 'revocado'
        )
    );

COMMENT ON COLUMN auth_user_signing_key.private_key_purgado_at IS
    'Marca de purga del material privado (E17/F4): non-NULL ⟺ private_key_cifrado/nonce anulados. Solo credenciales revocadas se purgan; lo público (cert_pem, huellas, serial, revocado_*) queda intacto para verificación/revocation-status.';

-- Backfill: las credenciales SUPERSEDED por re-enroll se revocaban vía
-- `revoke()` que (antes de este incremento) NO sellaba `revocado_at` (solo
-- `updated_at`). El reloj de la ventana de purga es
-- `COALESCE(revocado_at, updated_at)`, y el re-wrap de KEK mueve `updated_at`
-- -> sin un `revocado_at` inmutable, esas filas nunca vencerían la ventana bajo
-- rotación (hallazgo Media de seguridad). Se congela su reloj al `updated_at`
-- actual. Idempotente (`WHERE ... IS NULL`). `revocado_por`/`motivo` quedan NULL
-- (el supersede no es una revocación atribuible a un actor).
UPDATE auth_user_signing_key
   SET revocado_at = updated_at
 WHERE estado = 'revocado' AND revocado_at IS NULL;
