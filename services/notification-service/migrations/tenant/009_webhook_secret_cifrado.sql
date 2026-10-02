-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Secreto HMAC de los webhooks CIFRADO EN REPOSO.
--
-- `secret` (003) guardaba en claro la clave con la que se firma cada entrega:
-- quien leyera la base, un respaldo o una inyección podía forjar entregas que el
-- receptor aceptaría. Ahora el secreto se guarda cifrado (AES-256-GCM, AAD =
-- tenant + id del webhook) con una KEK de la APLICACIÓN, que no está en la base.
--
-- Esta migración NO puede cifrar los existentes —la KEK no está en SQL—: añade
-- las columnas y cierra la puerta a escribir en claro de ahora en adelante. El
-- cifrado de las filas ya existentes (y dejar `secret` en NULL) lo hace la
-- aplicación: `python -m app.ops.cifrar_webhook_secrets`, idempotente, con asiento
-- en audit_log.

ALTER TABLE webhook_subscription
    ADD COLUMN IF NOT EXISTS secret_cifrado     BYTEA,
    ADD COLUMN IF NOT EXISTS secret_nonce       BYTEA,
    ADD COLUMN IF NOT EXISTS secret_kek_version INTEGER;

-- Las tres columnas van juntas o ninguna: un ciphertext sin su nonce o sin la
-- versión de KEK es un secreto irrecuperable.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_webhook_secret_cifrado_completo'
          AND conrelid = 'webhook_subscription'::regclass
    ) THEN
        ALTER TABLE webhook_subscription
            ADD CONSTRAINT chk_webhook_secret_cifrado_completo CHECK (
                (secret_cifrado IS NULL AND secret_nonce IS NULL AND secret_kek_version IS NULL)
                OR (secret_cifrado IS NOT NULL AND secret_nonce IS NOT NULL
                    AND secret_kek_version IS NOT NULL AND secret_kek_version >= 1)
            );
    END IF;
    -- Nunca el secreto en claro Y cifrado a la vez.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_webhook_secret_un_solo_formato'
          AND conrelid = 'webhook_subscription'::regclass
    ) THEN
        ALTER TABLE webhook_subscription
            ADD CONSTRAINT chk_webhook_secret_un_solo_formato CHECK (
                secret IS NULL OR secret_cifrado IS NULL
            );
    END IF;
    -- NOT VALID: no se escribe NINGÚN secreto nuevo en claro (INSERT y UPDATE se
    -- comprueban), pero las filas heredadas todavía en claro no bloquean la
    -- migración; el paso de la aplicación las cifra y entonces valida.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_webhook_secret_sin_claro'
          AND conrelid = 'webhook_subscription'::regclass
    ) THEN
        ALTER TABLE webhook_subscription
            ADD CONSTRAINT chk_webhook_secret_sin_claro CHECK (secret IS NULL) NOT VALID;
    END IF;
END $$;

COMMENT ON COLUMN webhook_subscription.secret IS
    'OBSOLETO: secreto en claro heredado. Debe quedar siempre NULL; ver secret_cifrado.';
COMMENT ON COLUMN webhook_subscription.secret_cifrado IS
    'Secreto HMAC cifrado con AES-256-GCM; AAD tenant:{slug}:webhook:{id}:secret.';
