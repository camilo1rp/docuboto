-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 010: 2FA por TOTP (RFC 6238) para la firma personal (E06,
-- elevando el OTP-correo del Inc.2). El secreto vive en AUTH-SERVICE, junto a
-- auth_users (D1 ownership) — signature-service NUNCA lo ve, solo llama al
-- endpoint interno de verificación.
--
-- Tabla SEPARADA de auth_users (no la carga en lecturas normales de auth_users):
-- 1:1 lógico por usuario, pero solo UNA fila activa/pendiente a la vez —
-- ``uq_auth_user_totp_activo`` es un índice único PARCIAL sobre estado <> 'revocado',
-- mismo patrón que ``uq_firma_challenge_activo`` (signature-service 011) para el
-- reemplazo idempotente en re-enroll (revoke-then-insert dentro de una transacción).
--
-- INVARIANTE RECTOR: el secreto se cifra en reposo con AESGCM (secret_cifrado +
-- secret_nonce) y solo se descifra en memoria al verificar/reenrolar/revocar con
-- step-up. NUNCA se persiste, loguea, ni figura en respuesta o evento algunos.
--
-- Rollback:
--   DROP TABLE IF EXISTS auth_user_totp;

CREATE TABLE IF NOT EXISTS auth_user_totp (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
    secret_cifrado    BYTEA NOT NULL,            -- AESGCM ciphertext del secreto TOTP (20 bytes)
    secret_nonce      BYTEA NOT NULL,             -- nonce aleatorio de 12 bytes, uno por fila
    algoritmo         TEXT NOT NULL DEFAULT 'SHA1'
                      CHECK (algoritmo IN ('SHA1')),
    digitos           SMALLINT NOT NULL DEFAULT 6,
    periodo           SMALLINT NOT NULL DEFAULT 30,
    estado            TEXT NOT NULL DEFAULT 'pendiente_activacion'
                      CHECK (estado IN ('pendiente_activacion', 'activo', 'revocado')),
    last_timestep     BIGINT NOT NULL DEFAULT 0,  -- anti-replay (RFC 6238): solo Tm > last_timestep
    failed_attempts   SMALLINT NOT NULL DEFAULT 0,
    locked_until      TIMESTAMPTZ,
    key_version       SMALLINT NOT NULL DEFAULT 1, -- rotación futura de totp_encryption_key
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    activated_at      TIMESTAMPTZ,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Un solo TOTP activo/pendiente por usuario — el enroll/re-enroll reemplaza
-- (revoca la fila vieja e inserta una nueva) dentro de la misma transacción.
CREATE UNIQUE INDEX IF NOT EXISTS uq_auth_user_totp_activo
    ON auth_user_totp (user_id)
    WHERE estado <> 'revocado';

CREATE INDEX IF NOT EXISTS ix_auth_user_totp_user ON auth_user_totp (user_id);

COMMENT ON TABLE auth_user_totp IS
    'Credencial TOTP (RFC 6238) para el segundo factor de la firma personal '
    '(E06). El secreto se cifra en reposo (AESGCM) y solo se descifra en '
    'memoria al verificar — NUNCA se persiste, loguea, ni figura en '
    'respuesta o evento algunos. GET /me/totp jamás lo devuelve.';

COMMENT ON COLUMN auth_user_totp.secret_cifrado IS
    'Ciphertext AESGCM del secreto (20 bytes). AAD = '
    '"tenant:{slug}:user:{user_id}:totp" — ata el ciphertext a la fila/tenant; '
    'movido a otra fila/tenant, el descifrado falla.';

COMMENT ON COLUMN auth_user_totp.last_timestep IS
    'Último timestep (T = floor(unix/periodo)) aceptado. El verify solo acepta '
    'Tm > last_timestep (anti-replay) — avance atómico en un único UPDATE '
    'condicional (WHERE last_timestep < Tm).';

COMMENT ON COLUMN auth_user_totp.failed_attempts IS
    'Contador de verificaciones fallidas (activate + verify comparten la misma '
    'fila). Al alcanzar totp_max_failed_attempts, locked_until = now() + '
    'totp_lockout_seconds; ninguno de los dos es reseteable por el caller.';
