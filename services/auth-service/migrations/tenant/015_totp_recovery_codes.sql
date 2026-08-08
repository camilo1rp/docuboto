-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 015: códigos de recuperación del 2FA TOTP (E06, brecha de
-- producto declarada en el roadmap).
--
-- QUÉ PROBLEMA CIERRA. El TOTP (migración 010) es un factor de POSESIÓN: el
-- secreto vive en el teléfono del usuario. Todas las salidas de esa credencial
-- --revocarla (`DELETE /me/totp`), reenrolarla (`POST /me/totp/enroll` sobre una
-- activa) y revocar la clave PERSONAL de firma (`DELETE /me/signing-key`)--
-- exigen un código TOTP vigente, es decir, exigen el mismo teléfono que se
-- perdió. Un usuario que pierde el dispositivo queda ENCERRADO: no puede
-- recuperar su segundo factor ni retirar su credencial de firma, y la única
-- salida era una intervención manual sobre la base de datos.
--
-- POR QUÉ UN HASH Y NO CIFRADO (a diferencia del secreto TOTP). El secreto TOTP
-- se CIFRA porque el servidor necesita recuperarlo en claro para calcular el
-- código esperado. Un código de recuperación no: solo hay que comprobar que el
-- que presenta el usuario es uno de los emitidos. Guardar un hash (scrypt, con
-- sal por código) hace que una copia de esta tabla NO permita autenticarse,
-- mientras que una copia de `auth_user_totp` más la KEK sí. Es una propiedad
-- estrictamente mejor y aquí es alcanzable, así que se toma.
--
-- FRONTERA DE USO (deliberada, no una omisión). Estos códigos sirven para
-- RECUPERAR LA CUENTA -- revocar/reenrolar el TOTP -- y NO para FIRMAR. El
-- endpoint interno `/internal/totp/verify`, que signature-service invoca al
-- firmar, no los acepta: un código de recuperación está escrito en papel o en
-- un gestor de contraseñas, no es posesión de un dispositivo, y admitirlo como
-- segundo factor de un acto de firma debilitaría la atribución que RF-FIR-13
-- persigue. Recuperar el acceso y firmar son actos distintos y se exigen
-- pruebas distintas.
--
-- Rollback:
--   DROP TABLE IF EXISTS auth_user_totp_recovery;

CREATE TABLE IF NOT EXISTS auth_user_totp_recovery (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Cuelga del TOTP, no del usuario: revocar o reenrolar la credencial
    -- INVALIDA sus códigos por ON DELETE CASCADE... salvo que la fila del TOTP
    -- no se borra, se marca 'revocado'. Por eso el servicio borra los códigos
    -- explícitamente al revocar/reenrolar, dentro de la MISMA transacción --
    -- un código que sobreviviera a la revocación sería una puerta abierta a
    -- una credencial que el usuario ya dio por retirada.
    totp_id       UUID NOT NULL REFERENCES auth_user_totp(id) ON DELETE CASCADE,
    user_id       UUID NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
    -- scrypt(code, salt) -- el código en claro NUNCA se persiste ni se loguea,
    -- y se muestra UNA sola vez en la respuesta que lo genera.
    code_hash     BYTEA NOT NULL,
    code_salt     BYTEA NOT NULL,
    -- Consumo de un solo uso. NULL = disponible. El consumo es un UPDATE
    -- condicional (`WHERE used_at IS NULL`) para que dos peticiones
    -- concurrentes con el mismo código no lo gasten dos veces.
    used_at       TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_auth_user_totp_recovery_totp
    ON auth_user_totp_recovery (totp_id);

-- El verify recorre los códigos NO usados del usuario (son ~10): índice parcial
-- para que ese barrido no toque los ya consumidos.
CREATE INDEX IF NOT EXISTS ix_auth_user_totp_recovery_disponibles
    ON auth_user_totp_recovery (user_id)
    WHERE used_at IS NULL;

COMMENT ON TABLE auth_user_totp_recovery IS
    'Códigos de recuperación de un solo uso del 2FA TOTP (E06). Sirven para '
    'RECUPERAR la cuenta (revocar/reenrolar el TOTP perdido), NUNCA para '
    'firmar: /internal/totp/verify no los acepta. Se guardan como hash scrypt '
    'con sal por código -- una copia de esta tabla no permite autenticarse.';

COMMENT ON COLUMN auth_user_totp_recovery.code_hash IS
    'scrypt(code_utf8, salt) con los parámetros de app/core/recovery_codes.py. '
    'El código en claro se muestra UNA vez, al generarlo, y no se persiste.';

COMMENT ON COLUMN auth_user_totp_recovery.used_at IS
    'NULL = disponible. El consumo es un UPDATE condicional WHERE used_at IS '
    'NULL, de modo que dos peticiones concurrentes con el mismo código no lo '
    'gastan dos veces (una gana, la otra ve 0 filas y lo trata como inválido).';
