-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 064 (FIRMA-2, ronda 3, ADR-030): SOBRE DE REVELACIÓN del fundamento.
--
-- Por cada constancia v3 y cada revocación v3, archive-service genera un XML
-- `{id, acto, texto, sal_hex, compromiso, algoritmo}`, lo sella con el sello institucional y lo
-- guarda como OBJETO PROPIO bajo Object-Lock. Así el par texto+sal (hasta ahora SOLO en la base)
-- tiene copia preservada y puede viajar en la transferencia secundaria al AGN. El XML del sobre
-- NO se guarda en la base (el texto y la sal ya están en sus columnas): solo la referencia al
-- objeto sellado, su huella y su nivel XAdES.
--
-- Constancias y revocaciones legadas (v1/v2, y las v3 anteriores a esta migración): sin sobre
-- (todas las columnas NULL). Ningún CHECK las obliga a tenerlo.
--
-- Cambios:
--   * 4 columnas nullable en cada tabla de acto: sobre_firma_id, sobre_minio_key, sobre_sha256,
--     sobre_xades_level.
--   * `artefacto` de las tablas `_worm` admite 'sobre' (además de 'sello' y 'personal'): mismo
--     ciclo de protección inicial, reintento y renovación CT que los demás eslabones.
--   * El trigger de la constancia exige, para re-declarar, que el sobre de la revocación
--     sustituida (si existe) también esté protegido.
--
-- Idempotente. search_path = tenant_{slug} (ADR-012).

ALTER TABLE transferencia_acta_firma_excepcion ADD COLUMN IF NOT EXISTS sobre_firma_id UUID;
ALTER TABLE transferencia_acta_firma_excepcion ADD COLUMN IF NOT EXISTS sobre_minio_key TEXT;
ALTER TABLE transferencia_acta_firma_excepcion ADD COLUMN IF NOT EXISTS sobre_sha256 TEXT;
ALTER TABLE transferencia_acta_firma_excepcion ADD COLUMN IF NOT EXISTS sobre_xades_level TEXT;

ALTER TABLE transferencia_acta_firma_excepcion_revocacion ADD COLUMN IF NOT EXISTS sobre_firma_id UUID;
ALTER TABLE transferencia_acta_firma_excepcion_revocacion ADD COLUMN IF NOT EXISTS sobre_minio_key TEXT;
ALTER TABLE transferencia_acta_firma_excepcion_revocacion ADD COLUMN IF NOT EXISTS sobre_sha256 TEXT;
ALTER TABLE transferencia_acta_firma_excepcion_revocacion ADD COLUMN IF NOT EXISTS sobre_xades_level TEXT;

COMMENT ON COLUMN transferencia_acta_firma_excepcion.sobre_minio_key IS
    'Clave en el almacén del sobre de revelación sellado (texto+sal del fundamento). NULL en constancias anteriores a la 064. Acceso solo mediado por archive-service (administración nivel 3, con asiento de lectura).';
COMMENT ON COLUMN transferencia_acta_firma_excepcion_revocacion.sobre_minio_key IS
    'Clave en el almacén del sobre de revelación sellado de la revocación. NULL en revocaciones anteriores a la 064.';

DO $$
DECLARE
    t TEXT;
    c TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['transferencia_acta_firma_excepcion_worm',
                              'transferencia_acta_firma_excepcion_revocacion_worm'] LOOP
        -- El CHECK de `artefacto` nació en línea (nombre autogenerado): se localiza por su definición.
        FOR c IN SELECT conname FROM pg_constraint
                  WHERE conrelid = t::regclass AND contype = 'c'
                    AND pg_get_constraintdef(oid) LIKE '%artefacto%' LOOP
            EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I', t, c);
        END LOOP;
        EXECUTE format('ALTER TABLE %I ADD CONSTRAINT %I CHECK (artefacto IN (''sello'', ''personal'', ''sobre''))',
                       t, t || '_artefacto_check');
    END LOOP;
END;
$$;

CREATE OR REPLACE FUNCTION firma_excepcion_guard_excepcion() RETURNS trigger AS $$
DECLARE
    v_prev   transferencia_acta_firma_excepcion%ROWTYPE;
    v_rev    transferencia_acta_firma_excepcion_revocacion%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('acta_firma_rol'), hashtext(NEW.transferencia_id::text || ':' || NEW.rol));
    IF EXISTS (SELECT 1 FROM transferencias t
                WHERE t.id = NEW.transferencia_id
                  AND NEW.declarada_por::text IN (t.enviada_por, t.decidida_por, t.creada_por)) THEN
        RAISE EXCEPTION 'quien declara la excepción de firma no puede ser parte de la transferencia'
            USING ERRCODE = '23514', CONSTRAINT = 'ck_firma_excepcion_declarante_es_parte';
    END IF;
    -- Cualquier firma personal del rol cierra la cadena: la parte pudo firmar en la ventana en que
    -- la excepción estuvo revocada, y la firma prevalece sobre toda constancia posterior.
    IF EXISTS (SELECT 1 FROM transferencia_acta_firma_personal fp
                WHERE fp.transferencia_id = NEW.transferencia_id AND fp.rol = NEW.rol) THEN
        RAISE EXCEPTION 'el rol % del acta ya firmó personalmente: no admite excepción de firma', NEW.rol
            USING ERRCODE = '23514', CONSTRAINT = 'ck_firma_excepcion_parte_ya_firmo';
    END IF;
    IF NEW.sustituye_a IS NOT NULL THEN
        SELECT * INTO v_prev FROM transferencia_acta_firma_excepcion WHERE id = NEW.sustituye_a;
        IF NOT FOUND OR v_prev.transferencia_id <> NEW.transferencia_id OR v_prev.rol <> NEW.rol
           OR NEW.ordinal <> v_prev.ordinal + 1 THEN
            RAISE EXCEPTION 'la constancia no continúa la cadena del rol %', NEW.rol
                USING ERRCODE = '23514', CONSTRAINT = 'ck_firma_excepcion_cadena_invalida';
        END IF;
        SELECT * INTO v_rev FROM transferencia_acta_firma_excepcion_revocacion WHERE excepcion_id = v_prev.id;
        -- La revocación debe existir, estar sellada y tener TODOS sus artefactos con Object-Lock
        -- confirmado: la constancia sustituida es prueba y su baja también.
        IF NOT FOUND OR v_rev.firma_id IS NULL
           OR NOT EXISTS (SELECT 1 FROM transferencia_acta_firma_excepcion_revocacion_worm w
                           WHERE w.revocacion_id = v_rev.id AND w.artefacto = 'sello'
                             AND w.worm_protegido_at IS NOT NULL)
           OR (v_rev.firma_personal_id IS NOT NULL AND NOT EXISTS (
                   SELECT 1 FROM transferencia_acta_firma_excepcion_revocacion_worm w
                    WHERE w.revocacion_id = v_rev.id AND w.artefacto = 'personal'
                      AND w.worm_protegido_at IS NOT NULL))
           OR (v_rev.sobre_minio_key IS NOT NULL AND NOT EXISTS (
                   SELECT 1 FROM transferencia_acta_firma_excepcion_revocacion_worm w
                    WHERE w.revocacion_id = v_rev.id AND w.artefacto = 'sobre'
                      AND w.worm_protegido_at IS NOT NULL)) THEN
            RAISE EXCEPTION 'la revocación de la constancia sustituida no está sellada y protegida WORM'
                USING ERRCODE = '23514', CONSTRAINT = 'ck_firma_excepcion_revocacion_sin_proteger';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Rollback manual (no automatizado; solo viable sin filas 'sobre'):
-- (restaurar firma_excepcion_guard_excepcion() de la 062 y el CHECK de `artefacto` a ('sello','personal'))
-- ALTER TABLE ... DROP COLUMN sobre_firma_id, sobre_minio_key, sobre_sha256, sobre_xades_level;
