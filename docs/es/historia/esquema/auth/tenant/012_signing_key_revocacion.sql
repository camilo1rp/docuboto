-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 012: revocación de certificados de firma personal PKI +
-- anclaje de la sub-CA de usuarios (E17/F4, Incremento 2 — cierra la Baja #4
-- del incremento anterior). Aditiva sobre ``011_user_signing_key.sql``.
--
-- ``estado`` YA admite 'revocado' desde la 011 (CHECK sin cambios) — este
-- archivo solo añade las columnas que registran CUÁNDO/QUIÉN/POR QUÉ se
-- revocó una credencial, y las dos huellas SHA-256 que ancla la Baja #4:
--
--   - ``certificado_fingerprint``: huella del certificado LEAF (del usuario)
--     — la MISMA que ``activar_certificado`` ya computaba en memoria
--     (``_cert_fingerprint``) pero nunca persistía; ahora se guarda para que
--     ``GET /internal/signing-key/revocation-status`` (signature-service)
--     pueda resolver el estado de revocación por huella SIN reconstruir el
--     certificado.
--   - ``user_ca_fingerprint``: huella de la sub-CA de usuarios (``ca-users.crt``,
--     montada SOLO por su certificado PÚBLICO — nunca ``ca.key``) que emitió
--     ese leaf, verificada por ``activar_certificado`` vía
--     ``leaf.verify_directly_issued_by(sub_ca)`` (fail-closed: un leaf que no
--     encadena con la sub-CA montada nunca llega a 'activo').
--
-- BACKFILL: las filas existentes (``activo``/``revocado`` con ``cert_pem``
-- poblado) no tienen ``certificado_fingerprint`` — se computa aquí mismo
-- (SHA-256 del DER, decodificado del PEM) con ``pgcrypto.digest()`` para que
-- la revocación cubra TAMBIÉN las firmas personales emitidas antes de este
-- incremento (el propio requisito D de la Baja #4: "la revocación cubre
-- TODAS las firmas personales, por identidad del cert"). ``user_ca_fingerprint``
-- NO se backfillea (no hay forma de saber, para una fila histórica, qué sub-CA
-- la emitió sin volver a verificar la cadena — queda NULL; el efecto es que
-- una firma Inc.1 histórica simplemente no entra a la rama de anclaje de
-- ``verify_xades`` — grandfather ESTRUCTURAL, ver docstring de esa función).
--
-- Rollback:
--   DROP INDEX IF EXISTS ix_auth_user_signing_key_certificado_fingerprint;
--   ALTER TABLE auth_user_signing_key
--       DROP COLUMN IF EXISTS revocado_at,
--       DROP COLUMN IF EXISTS revocado_por,
--       DROP COLUMN IF EXISTS motivo_revocacion,
--       DROP COLUMN IF EXISTS certificado_fingerprint,
--       DROP COLUMN IF EXISTS user_ca_fingerprint;

-- pgcrypto (digest()) para el backfill de abajo — instalado en `public`
-- (ya presente en el search_path de toda migración de tenant, ver
-- migrations/runner.py) para que se resuelva sin importar qué tenant
-- ejecute esta migración primero. Idempotente a nivel de BASE DE DATOS
-- (CREATE EXTENSION IF NOT EXISTS no falla si otro tenant ya la instaló).
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;

ALTER TABLE auth_user_signing_key
    ADD COLUMN IF NOT EXISTS revocado_at            TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS revocado_por            UUID REFERENCES auth_users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS motivo_revocacion        TEXT,
    ADD COLUMN IF NOT EXISTS certificado_fingerprint  TEXT,
    ADD COLUMN IF NOT EXISTS user_ca_fingerprint       TEXT;

-- Lookup por huella del certificado LEAF — usado por
-- GET /internal/signing-key/revocation-status (signature-service, D-02).
-- Parcial (WHERE NOT NULL): la inmensa mayoría de filas 'pendiente_emision'
-- nunca tienen huella (cert_pem NULL hasta la activación).
CREATE INDEX IF NOT EXISTS ix_auth_user_signing_key_certificado_fingerprint
    ON auth_user_signing_key (certificado_fingerprint)
    WHERE certificado_fingerprint IS NOT NULL;

-- Backfill: SHA-256 del DER del certificado LEAF (mismo cálculo que
-- ``cert.fingerprint(hashes.SHA256()).hex()`` en Python — ver
-- app/services/signing_key_service.py::_cert_fingerprint). ``cert_pem``
-- SIEMPRE debería contener un único certificado (así lo escribe
-- ``activar_certificado``), pero si una fila histórica guardara una CADENA
-- (varios bloques BEGIN/END concatenados, leaf+CA), hashear el PEM completo
-- daría una huella que NO coincide con la del leaf que Python calcula —
-- ``revocation-status`` reportaría ``desconocido`` de forma espuria
-- (fail-closed, sin fuga, pero imperfecto). Por eso el ``substring(... from
-- '...')`` de abajo aísla PRIMERO el primer bloque
-- ``-----BEGIN CERTIFICATE----- ... -----END CERTIFICATE-----`` (no-greedy,
-- ARE de Postgres) ANTES de quitar la armadura/espacios y decodificar
-- base64 — el DER resultante es siempre el del leaf, sin importar cuántos
-- certificados adicionales (CA intermedia, etc.) traiga la fila.
UPDATE auth_user_signing_key
SET certificado_fingerprint = encode(
    public.digest(
        decode(
            regexp_replace(
                substring(
                    cert_pem
                    from '-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----'
                ),
                '-----BEGIN CERTIFICATE-----|-----END CERTIFICATE-----|\s',
                '',
                'g'
            ),
            'base64'
        ),
        'sha256'
    ),
    'hex'
)
WHERE cert_pem IS NOT NULL
  AND certificado_fingerprint IS NULL;

COMMENT ON COLUMN auth_user_signing_key.revocado_at IS
    'Momento de la revocación (self-revoke o admin-revoke) — NULL mientras '
    'estado <> ''revocado''. Fuente de verdad de GET .../revocation-status.';

COMMENT ON COLUMN auth_user_signing_key.revocado_por IS
    'auth_users.id del actor que revocó (el propio titular en self-revoke, '
    'un administrador en admin-revoke). ON DELETE SET NULL: no bloquea '
    'borrar al admin, y la fila revocada en sí ya cae por CASCADE si se '
    'borra su PROPIO titular (user_id, ver 011).';

COMMENT ON COLUMN auth_user_signing_key.motivo_revocacion IS
    'Motivo opcional en texto libre aportado por quien revoca (self-revoke '
    'o admin-revoke). Nunca material de firma.';

COMMENT ON COLUMN auth_user_signing_key.certificado_fingerprint IS
    'SHA-256 hex del DER del certificado LEAF (mismo valor que '
    'certificado_fingerprint en signature-service.firma) — clave de '
    'búsqueda de GET /internal/signing-key/revocation-status. Poblada por '
    'activar_certificado() a partir del Incremento 2; backfilleada aquí '
    'para las filas emitidas antes.';

COMMENT ON COLUMN auth_user_signing_key.user_ca_fingerprint IS
    'SHA-256 hex de la sub-CA de usuarios (ca-users.crt) que emitió este '
    'leaf — verificada por activar_certificado() vía '
    'leaf.verify_directly_issued_by(sub_ca), fail-closed. NULL en filas '
    'emitidas antes del Incremento 2 (grandfather estructural, sin '
    'backfill posible sin volver a verificar la cadena).';
