-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 011: firma electrónica PERSONAL PKI por-usuario (E17/F4,
-- Incremento 1) — custodia SERVIDOR (postura B), marco honesto "no
-- acreditado" (mismo nivel legal art. 7 que la firma electrónica simple),
-- forward-compatible a HSM (ver app/core/signing_keys.py).
--
-- CAVEAT explícito (C7, honestidad RF-FIR-15): custodia SERVIDOR ⇒ NO hay
-- sole-control del titular sobre su clave (las columnas de este archivo
-- cifran la clave EN REPOSO — property de SEGURIDAD: no se filtra — pero
-- el custodio conserva acceso técnico a ella — property LEGAL, distinta:
-- no puede excluirse como autor material). Este esquema es firma
-- electrónica art. 7 Ley 527/1999 (atribución + integridad verificable),
-- NO da no-repudio oponible contra el operador/institución y NO equivale a
-- firma digital cualificada (art. 28); ese tier exige clave bajo control
-- EXCLUSIVO del firmante + CA acreditada ONAC — roadmap HSM/PKI.
--
-- Espeja el patrón de ``010_totp.sql``: tabla SEPARADA de auth_users, 1:1
-- lógico por usuario pero solo UNA fila no-revocada a la vez
-- (``uq_auth_user_signing_key_activa``, índice único PARCIAL sobre
-- estado <> 'revocado' — mismo reemplazo idempotente en re-enroll que
-- ``uq_auth_user_totp_activo``).
--
-- INVARIANTE RECTOR (no negociable, ver CLAUDE.md de este incremento): la
-- clave privada del usuario JAMÁS sale de este servicio en claro — se cifra
-- en reposo con AESGCM (``private_key_cifrado`` + ``private_key_nonce``) y
-- solo se descifra en memoria, dentro de auth-service, para el tiempo mínimo
-- indispensable de firmar UN digest (``sign_digest``, dentro de la misma
-- transacción que verifica-y-consume el TOTP). Ni un endpoint, log, evento o
-- entrada de auditoría de este servicio emite la clave privada — ni una sola
-- vez (a diferencia del secreto TOTP, que SÍ se revela una vez en el enroll).
--
-- ``estado``: 'pendiente_emision' (keygen + CSR generados, a la espera de que
-- el emisor OFFLINE — scripts/gen-user-cert.sh, o el flujo de test con CA en
-- memoria — cargue el certificado firmado), 'activo' (certificado cargado,
-- utilizable para firmar), 'revocado' (reemplazada por un re-enroll, o
-- revocada explícitamente).
--
-- Rollback:
--   DROP TABLE IF EXISTS auth_user_signing_key;

CREATE TABLE IF NOT EXISTS auth_user_signing_key (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id               UUID NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
    cert_pem              TEXT,               -- NULL hasta 'activo' (emisión offline)
    csr_pem               TEXT,               -- CSR generado en el enroll (público)
    private_key_cifrado   BYTEA NOT NULL,      -- AESGCM ciphertext de la clave privada (PEM)
    private_key_nonce     BYTEA NOT NULL,      -- nonce aleatorio de 12 bytes, uno por fila
    subject_dn            TEXT,
    cargo                 TEXT,
    serial                TEXT,               -- serial del certificado emitido (hex)
    not_before            TIMESTAMPTZ,
    not_after             TIMESTAMPTZ,
    key_version           SMALLINT NOT NULL DEFAULT 1, -- rotación futura de signing_key_encryption_key
    estado                TEXT NOT NULL DEFAULT 'pendiente_emision'
                          CHECK (estado IN ('pendiente_emision', 'activo', 'revocado')),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    activated_at          TIMESTAMPTZ,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Una sola clave de firma no-revocada por usuario — el enroll/re-enroll
-- reemplaza (revoca la fila vieja e inserta una nueva) dentro de la misma
-- transacción, mismo patrón que ``uq_auth_user_totp_activo``.
CREATE UNIQUE INDEX IF NOT EXISTS uq_auth_user_signing_key_activa
    ON auth_user_signing_key (user_id)
    WHERE estado <> 'revocado';

CREATE INDEX IF NOT EXISTS ix_auth_user_signing_key_user ON auth_user_signing_key (user_id);

COMMENT ON TABLE auth_user_signing_key IS
    'Clave de firma PKI personal por-usuario (E17/F4, Incremento 1, custodia '
    'servidor). La clave privada se cifra en reposo (AESGCM) y solo se '
    'descifra en memoria dentro de auth-service, para firmar UN digest — '
    'NUNCA se persiste en claro, ni se loguea, ni figura en respuesta o '
    'evento algunos. GET /me/signing-key jamás la devuelve.';

COMMENT ON COLUMN auth_user_signing_key.private_key_cifrado IS
    'Ciphertext AESGCM de la clave privada RSA-2048 (PEM). AAD = '
    '"tenant:{slug}:user:{user_id}:signing" — ata el ciphertext a la '
    'fila/tenant; movido a otra fila/tenant, el descifrado falla.';

COMMENT ON COLUMN auth_user_signing_key.cert_pem IS
    'Certificado X.509 emitido OFFLINE por scripts/gen-user-cert.sh (o por '
    'una CA de test en memoria en los tests) contra el CSR de esta fila. '
    'NULL mientras estado=''pendiente_emision''. Material PÚBLICO.';

COMMENT ON COLUMN auth_user_signing_key.estado IS
    'pendiente_emision: keygen+CSR listos, a la espera del certificado '
    'emitido offline. activo: certificado cargado, utilizable para firmar. '
    'revocado: reemplazada por un re-enroll o revocada explícitamente.';
