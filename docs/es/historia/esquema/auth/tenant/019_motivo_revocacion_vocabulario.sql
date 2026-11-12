-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Vocabulario cerrado de `motivo_revocacion` (T8, AREA-04-13).
--
-- `/verify` decide si una revocación POSTERIOR a la firma la deja valida: eso
-- solo es sostenible cuando la clave no se comprometió (ETSI EN 319 102-1). Con
-- texto libre el servidor no puede distinguir «salió el funcionario» de «me
-- robaron la clave». Los códigos siguen RFC 5280 §5.3.1 (reasonCode).
--
-- Las filas existentes con texto libre pasan a `unspecified` (el texto original
-- sigue en `public.audit_log`, payload del asiento `signing_key.revocada`).
-- Idempotente: reejecutable sin efecto.

UPDATE auth_user_signing_key
   SET motivo_revocacion = 'unspecified'
 WHERE motivo_revocacion IS NOT NULL
   AND motivo_revocacion NOT IN (
       'key_compromise', 'superseded', 'cessation_of_operation',
       'affiliation_changed', 'privilege_withdrawn', 'unspecified'
   );

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'auth_user_signing_key_motivo_revocacion_vocab'
           AND conrelid = 'auth_user_signing_key'::regclass
    ) THEN
        ALTER TABLE auth_user_signing_key
            ADD CONSTRAINT auth_user_signing_key_motivo_revocacion_vocab
            CHECK (motivo_revocacion IS NULL OR motivo_revocacion IN (
                'key_compromise', 'superseded', 'cessation_of_operation',
                'affiliation_changed', 'privilege_withdrawn', 'unspecified'
            ));
    END IF;
END
$$;

COMMENT ON COLUMN auth_user_signing_key.motivo_revocacion IS
    'Código cerrado (RFC 5280 reasonCode): key_compromise | superseded | '
    'cessation_of_operation | affiliation_changed | privilege_withdrawn | '
    'unspecified. NULL = no informado (se trata como unspecified). '
    'key_compromise invalida hacia atrás las firmas previas en /verify.';
