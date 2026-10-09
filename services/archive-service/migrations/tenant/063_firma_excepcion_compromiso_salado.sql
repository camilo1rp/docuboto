-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


-- Tenant migration 063 (FIRMA-2, ronda de auditoría A1/A6): el fundamento de la constancia de
-- «firma inobtenible» y de su revocación se huella con un COMPROMISO CON SAL POR REGISTRO,
-- `SHA-256(sal_32B || texto_utf8)`, en lugar del HMAC con clave de plataforma de la 062 (que un
-- tercero —el AGN— no puede verificar sin recibir la clave de toda la entidad). Y la revocación
-- gana `causa` de vocabulario cerrado (A6).
--
-- Versionado (decisión): la constancia pasa a `constancia_version = 3` y la revocación a
-- `revocacion_version = 3`. Las filas v2 que ya existan (se escribieron con HMAC; la v2 nunca se
-- publicó fuera de desarrollo, y los datos locales son de prueba) se CONSERVAN como legadas: sus
-- columnas `fundamento_hmac*` no se tocan ni se borran (la tabla es append-only), no se
-- verifican por compromiso y su verificación de fundamento responde `verificable=false`.
--
-- La sal NO es WORM: vive en la base, junto al fundamento (que tampoco lo es). Sin sal no se
-- puede recomputar el compromiso, así que exponerla solo a administración nivel 3 y nunca a
-- `audit_log` es lo que impide el ataque por diccionario.
--
-- Idempotente: ADD COLUMN IF NOT EXISTS y DROP CONSTRAINT IF EXISTS + ADD guardado por pg_constraint.
-- search_path = tenant_{slug} (ADR-012).

ALTER TABLE transferencia_acta_firma_excepcion ADD COLUMN IF NOT EXISTS fundamento_sal BYTEA;
ALTER TABLE transferencia_acta_firma_excepcion ADD COLUMN IF NOT EXISTS fundamento_compromiso TEXT;
ALTER TABLE transferencia_acta_firma_excepcion ADD COLUMN IF NOT EXISTS fundamento_compromiso_algoritmo TEXT;

ALTER TABLE transferencia_acta_firma_excepcion_revocacion ADD COLUMN IF NOT EXISTS fundamento_sal BYTEA;
ALTER TABLE transferencia_acta_firma_excepcion_revocacion ADD COLUMN IF NOT EXISTS fundamento_compromiso TEXT;
ALTER TABLE transferencia_acta_firma_excepcion_revocacion ADD COLUMN IF NOT EXISTS fundamento_compromiso_algoritmo TEXT;
ALTER TABLE transferencia_acta_firma_excepcion_revocacion ADD COLUMN IF NOT EXISTS causa TEXT;

COMMENT ON COLUMN transferencia_acta_firma_excepcion.fundamento_sal IS
    'Sal aleatoria de 32 bytes del compromiso del fundamento (constancia v3). SECRETA: solo administración nivel 3; nunca en audit_log. No es WORM.';
COMMENT ON COLUMN transferencia_acta_firma_excepcion.fundamento_compromiso IS
    'SHA-256(sal || texto_utf8) en hex (constancia v3). NULL en constancias v1/v2 (v2 usa fundamento_hmac, legado).';
COMMENT ON COLUMN transferencia_acta_firma_excepcion_revocacion.causa IS
    'Causa de la revocación (vocabulario cerrado): error_de_declaracion | parte_puede_firmar | otro. NULL en revocaciones anteriores a la 063.';

DO $$
BEGIN
    -- Constancia: v2 exige HMAC (legado); v3 exige compromiso salado completo.
    ALTER TABLE transferencia_acta_firma_excepcion DROP CONSTRAINT IF EXISTS ck_firma_excepcion_v2_completa;
    ALTER TABLE transferencia_acta_firma_excepcion
        ADD CONSTRAINT ck_firma_excepcion_v2_completa
        CHECK ((ordinal = 1 OR constancia_version >= 2)
               AND (constancia_version < 2 OR (
                    referencia_anexo_id IS NOT NULL
                    AND referencia_radicado_numero IS NOT NULL
                    AND referencia_anexo_sha256 IS NOT NULL
                    AND declarante_fuente IS NOT NULL
                    AND parte_fuente IS NOT NULL
                    AND firma_id IS NOT NULL
                    AND (constancia_version >= 3 OR (
                            fundamento_hmac IS NOT NULL
                            AND fundamento_hmac_key_version IS NOT NULL
                            AND fundamento_hmac_algoritmo IS NOT NULL))
                    AND (constancia_version < 3 OR (
                            fundamento_sal IS NOT NULL
                            AND octet_length(fundamento_sal) = 32
                            AND fundamento_compromiso IS NOT NULL
                            AND fundamento_compromiso_algoritmo = 'SHA-256-salted')))));

    -- Revocación: v2 exige HMAC (legado); v3 exige compromiso salado y causa.
    ALTER TABLE transferencia_acta_firma_excepcion_revocacion DROP CONSTRAINT IF EXISTS ck_firma_excepcion_revocacion_v2_completa;
    ALTER TABLE transferencia_acta_firma_excepcion_revocacion
        ADD CONSTRAINT ck_firma_excepcion_revocacion_v2_completa
        CHECK (revocacion_version < 2 OR (
                    firma_id IS NOT NULL
                    AND revocante_fuente IS NOT NULL
                    AND (revocacion_version >= 3 OR (
                            fundamento_hmac IS NOT NULL
                            AND fundamento_hmac_key_version IS NOT NULL
                            AND fundamento_hmac_algoritmo IS NOT NULL))
                    AND (revocacion_version < 3 OR (
                            fundamento_sal IS NOT NULL
                            AND octet_length(fundamento_sal) = 32
                            AND fundamento_compromiso IS NOT NULL
                            AND fundamento_compromiso_algoritmo = 'SHA-256-salted'
                            AND causa IS NOT NULL))));

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_firma_excepcion_revocacion_causa'
                      AND conrelid = 'transferencia_acta_firma_excepcion_revocacion'::regclass) THEN
        ALTER TABLE transferencia_acta_firma_excepcion_revocacion
            ADD CONSTRAINT ck_firma_excepcion_revocacion_causa
            CHECK (causa IS NULL OR causa IN ('error_de_declaracion', 'parte_puede_firmar', 'otro'));
    END IF;
END;
$$;

-- Rollback manual (no automatizado; solo viable sin filas v3):
-- ALTER TABLE transferencia_acta_firma_excepcion_revocacion DROP CONSTRAINT IF EXISTS ck_firma_excepcion_revocacion_causa;
-- (restaurar ck_firma_excepcion_v2_completa y ck_firma_excepcion_revocacion_v2_completa de la 062)
-- ALTER TABLE ... DROP COLUMN fundamento_sal, fundamento_compromiso, fundamento_compromiso_algoritmo, causa;
