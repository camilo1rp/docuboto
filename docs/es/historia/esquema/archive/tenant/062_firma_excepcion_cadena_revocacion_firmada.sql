-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


-- Tenant migration 062 (FIRMA-2, ADR-030 addendum): la constancia de «firma inobtenible» se
-- puede RE-DECLARAR tras revocarla (cadena lineal) y la REVOCACIÓN pasa a ser prueba (firmada y
-- protegida WORM como la constancia). search_path = tenant_{slug} (ADR-012).
--
-- Cambios:
--  1) CADENA. `transferencia_acta_firma_excepcion` pierde UNIQUE (transferencia_id, rol) y gana
--     `ordinal` + `sustituye_a`: UNIQUE (transferencia_id, rol, ordinal) y UNIQUE (sustituye_a)
--     (cadena lineal), con CHECK de coherencia (ordinal 1 <=> sin sustituye_a).
--  2) CONSTANCIA v2 autosuficiente (`constancia_version` = 2): número del radicado de soporte,
--     SHA-256 del anexo (el anexo es obligatorio), nombre/cargo de declarante y parte como
--     INSTANTÁNEA resuelta por el servidor, y `fundamento_hmac` (HMAC-SHA256 por entidad, clave
--     derivada en signature-service) en lugar de un SHA-256 plano atacable por diccionario.
--     Las filas anteriores (version 1) quedan como están: NULL en todo lo nuevo.
--  3) REVOCACIÓN firmada: XML canónico + huella + sello institucional + firma personal de quien
--     revoca (si tiene certificado activo) + instantánea de identidad + HMAC del fundamento.
--     Tabla `..._revocacion_worm` con el estado Object-Lock de sus dos artefactos.
--  4) El trigger de la constancia exige, para ordinal > 1, que la revocación de la sustituida
--     esté SELLADA y que TODOS sus artefactos estén protegidos WORM
--     (CONSTRAINT `ck_firma_excepcion_revocacion_sin_proteger`): si no, el motor rechaza la
--     re-declaración aunque el servicio se equivoque. Y si la parte firmó personalmente en la
--     ventana revocada, la cadena queda cerrada (`ck_firma_excepcion_parte_ya_firmo`).
--
-- Una revocación LEGADA (anterior a esta migración) no tiene sello: no admite re-declaración
-- (queda definitiva); la excepción se declara de nuevo solo si la anterior fue revocada con el
-- acto firmado.
--
-- Idempotente: ADD COLUMN IF NOT EXISTS, CREATE TABLE IF NOT EXISTS, CREATE OR REPLACE FUNCTION,
-- DROP TRIGGER IF EXISTS y ADD CONSTRAINT guardado por pg_constraint.

-- 1) Cadena -------------------------------------------------------------------------------------
ALTER TABLE transferencia_acta_firma_excepcion ADD COLUMN IF NOT EXISTS ordinal INTEGER NOT NULL DEFAULT 1;
ALTER TABLE transferencia_acta_firma_excepcion
    ADD COLUMN IF NOT EXISTS sustituye_a UUID REFERENCES transferencia_acta_firma_excepcion(id);
ALTER TABLE transferencia_acta_firma_excepcion
    ADD COLUMN IF NOT EXISTS constancia_version SMALLINT NOT NULL DEFAULT 1;

-- 2) Constancia v2 ------------------------------------------------------------------------------
ALTER TABLE transferencia_acta_firma_excepcion ADD COLUMN IF NOT EXISTS referencia_radicado_numero TEXT;
ALTER TABLE transferencia_acta_firma_excepcion ADD COLUMN IF NOT EXISTS referencia_anexo_sha256 TEXT;
ALTER TABLE transferencia_acta_firma_excepcion ADD COLUMN IF NOT EXISTS declarante_nombre TEXT;
ALTER TABLE transferencia_acta_firma_excepcion ADD COLUMN IF NOT EXISTS declarante_cargo TEXT;
ALTER TABLE transferencia_acta_firma_excepcion ADD COLUMN IF NOT EXISTS declarante_fuente TEXT;
ALTER TABLE transferencia_acta_firma_excepcion ADD COLUMN IF NOT EXISTS parte_nombre TEXT;
ALTER TABLE transferencia_acta_firma_excepcion ADD COLUMN IF NOT EXISTS parte_cargo TEXT;
ALTER TABLE transferencia_acta_firma_excepcion ADD COLUMN IF NOT EXISTS parte_fuente TEXT;
ALTER TABLE transferencia_acta_firma_excepcion ADD COLUMN IF NOT EXISTS fundamento_hmac TEXT;
ALTER TABLE transferencia_acta_firma_excepcion ADD COLUMN IF NOT EXISTS fundamento_hmac_key_version INTEGER;
ALTER TABLE transferencia_acta_firma_excepcion ADD COLUMN IF NOT EXISTS fundamento_hmac_algoritmo TEXT;

COMMENT ON COLUMN transferencia_acta_firma_excepcion.ordinal IS
    'Posición en la cadena de constancias del rol: 1 = primera; n+1 = re-declaración tras revocar la n.';
COMMENT ON COLUMN transferencia_acta_firma_excepcion.sustituye_a IS
    'Constancia (revocada) a la que sustituye esta; NULL solo en la primera de la cadena.';
COMMENT ON COLUMN transferencia_acta_firma_excepcion.fundamento_hmac IS
    'HMAC-SHA256 del fundamento con la clave por entidad (signature-service, HKDF fundamento_excepcion). NULL en constancias v1.';
COMMENT ON COLUMN transferencia_acta_firma_excepcion.declarante_fuente IS
    'De dónde salió el nombre del declarante: certificado (CN del certificado de firma) | usuario (username) | no_resuelta.';

-- 3) Revocación firmada -------------------------------------------------------------------------
ALTER TABLE transferencia_acta_firma_excepcion_revocacion
    ADD COLUMN IF NOT EXISTS revocacion_version SMALLINT NOT NULL DEFAULT 1;
ALTER TABLE transferencia_acta_firma_excepcion_revocacion ADD COLUMN IF NOT EXISTS revocacion_xml TEXT;
ALTER TABLE transferencia_acta_firma_excepcion_revocacion ADD COLUMN IF NOT EXISTS revocacion_sha256 TEXT;
ALTER TABLE transferencia_acta_firma_excepcion_revocacion ADD COLUMN IF NOT EXISTS firma_id UUID;
ALTER TABLE transferencia_acta_firma_excepcion_revocacion ADD COLUMN IF NOT EXISTS minio_key_firmado TEXT;
ALTER TABLE transferencia_acta_firma_excepcion_revocacion ADD COLUMN IF NOT EXISTS xades_level TEXT;
ALTER TABLE transferencia_acta_firma_excepcion_revocacion ADD COLUMN IF NOT EXISTS firma_personal_id UUID;
ALTER TABLE transferencia_acta_firma_excepcion_revocacion ADD COLUMN IF NOT EXISTS minio_key_firmado_personal TEXT;
ALTER TABLE transferencia_acta_firma_excepcion_revocacion ADD COLUMN IF NOT EXISTS xades_level_personal TEXT;
ALTER TABLE transferencia_acta_firma_excepcion_revocacion ADD COLUMN IF NOT EXISTS revocante_nombre TEXT;
ALTER TABLE transferencia_acta_firma_excepcion_revocacion ADD COLUMN IF NOT EXISTS revocante_cargo TEXT;
ALTER TABLE transferencia_acta_firma_excepcion_revocacion ADD COLUMN IF NOT EXISTS revocante_fuente TEXT;
ALTER TABLE transferencia_acta_firma_excepcion_revocacion ADD COLUMN IF NOT EXISTS fundamento_hmac TEXT;
ALTER TABLE transferencia_acta_firma_excepcion_revocacion ADD COLUMN IF NOT EXISTS fundamento_hmac_key_version INTEGER;
ALTER TABLE transferencia_acta_firma_excepcion_revocacion ADD COLUMN IF NOT EXISTS fundamento_hmac_algoritmo TEXT;

COMMENT ON COLUMN transferencia_acta_firma_excepcion_revocacion.revocacion_xml IS
    'XML canónico firmado (urn:orpycamcp:acta-firma-excepcion-revocacion:v1). NULL en revocaciones anteriores a la 062 (sin firma, definitivas).';

-- 4) Restricciones ------------------------------------------------------------------------------
DO $$
DECLARE
    r RECORD;
BEGIN
    -- UNIQUE (transferencia_id, rol) de la 059, cualquiera sea su nombre autogenerado.
    FOR r IN
        SELECT c.conname
          FROM pg_constraint c
         WHERE c.conrelid = 'transferencia_acta_firma_excepcion'::regclass
           AND c.contype = 'u'
           AND (SELECT array_agg(a.attname::text ORDER BY a.attname)
                  FROM unnest(c.conkey) k JOIN pg_attribute a
                    ON a.attrelid = c.conrelid AND a.attnum = k) = ARRAY['rol', 'transferencia_id']
    LOOP
        EXECUTE format('ALTER TABLE transferencia_acta_firma_excepcion DROP CONSTRAINT %I', r.conname);
    END LOOP;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_firma_excepcion_cadena'
                      AND conrelid = 'transferencia_acta_firma_excepcion'::regclass) THEN
        ALTER TABLE transferencia_acta_firma_excepcion
            ADD CONSTRAINT uq_firma_excepcion_cadena UNIQUE (transferencia_id, rol, ordinal);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_firma_excepcion_sustituye_a'
                      AND conrelid = 'transferencia_acta_firma_excepcion'::regclass) THEN
        ALTER TABLE transferencia_acta_firma_excepcion
            ADD CONSTRAINT uq_firma_excepcion_sustituye_a UNIQUE (sustituye_a);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_firma_excepcion_ordinal_coherente'
                      AND conrelid = 'transferencia_acta_firma_excepcion'::regclass) THEN
        ALTER TABLE transferencia_acta_firma_excepcion
            ADD CONSTRAINT ck_firma_excepcion_ordinal_coherente
            CHECK (ordinal >= 1 AND ((ordinal = 1) = (sustituye_a IS NULL)));
    END IF;
    -- v2: todo lo que hace autosuficiente a la constancia es obligatorio; y una re-declaración
    -- (ordinal > 1) solo existe en v2.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_firma_excepcion_v2_completa'
                      AND conrelid = 'transferencia_acta_firma_excepcion'::regclass) THEN
        ALTER TABLE transferencia_acta_firma_excepcion
            ADD CONSTRAINT ck_firma_excepcion_v2_completa
            CHECK ((ordinal = 1 OR constancia_version >= 2)
                   AND (constancia_version < 2 OR (
                        referencia_anexo_id IS NOT NULL
                        AND referencia_radicado_numero IS NOT NULL
                        AND referencia_anexo_sha256 IS NOT NULL
                        AND fundamento_hmac IS NOT NULL
                        AND fundamento_hmac_key_version IS NOT NULL
                        AND fundamento_hmac_algoritmo IS NOT NULL
                        AND declarante_fuente IS NOT NULL
                        AND parte_fuente IS NOT NULL
                        AND firma_id IS NOT NULL)));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_firma_excepcion_revocacion_sello_coherente'
                      AND conrelid = 'transferencia_acta_firma_excepcion_revocacion'::regclass) THEN
        ALTER TABLE transferencia_acta_firma_excepcion_revocacion
            ADD CONSTRAINT ck_firma_excepcion_revocacion_sello_coherente
            CHECK ((firma_id IS NULL) = (minio_key_firmado IS NULL)
                   AND (firma_id IS NULL) = (revocacion_xml IS NULL)
                   AND (firma_id IS NULL) = (revocacion_sha256 IS NULL)
                   AND (firma_personal_id IS NULL) = (minio_key_firmado_personal IS NULL)
                   AND (firma_personal_id IS NULL OR firma_id IS NOT NULL));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_firma_excepcion_revocacion_v2_completa'
                      AND conrelid = 'transferencia_acta_firma_excepcion_revocacion'::regclass) THEN
        ALTER TABLE transferencia_acta_firma_excepcion_revocacion
            ADD CONSTRAINT ck_firma_excepcion_revocacion_v2_completa
            CHECK (revocacion_version < 2 OR (
                        firma_id IS NOT NULL
                        AND fundamento_hmac IS NOT NULL
                        AND fundamento_hmac_key_version IS NOT NULL
                        AND fundamento_hmac_algoritmo IS NOT NULL
                        AND revocante_fuente IS NOT NULL));
    END IF;
END;
$$;

-- 5) Object-Lock de la revocación ---------------------------------------------------------------
-- Tabla APARTE y mutable (la revocación es append-only), gemela de la de la constancia (061).
CREATE TABLE IF NOT EXISTS transferencia_acta_firma_excepcion_revocacion_worm (
    revocacion_id               UUID NOT NULL
                                REFERENCES transferencia_acta_firma_excepcion_revocacion(id) ON DELETE CASCADE,
    artefacto                   TEXT NOT NULL CHECK (artefacto IN ('sello', 'personal')),
    worm_protegido_at           TIMESTAMPTZ,
    worm_object_id              UUID,
    worm_retain_until           TIMESTAMPTZ,
    worm_reconcile_intentos     INTEGER NOT NULL DEFAULT 0,
    worm_reconcile_proximo_at   TIMESTAMPTZ,
    worm_reconcile_ultimo_error TEXT,
    worm_reconcile_agotado      BOOLEAN NOT NULL DEFAULT FALSE,
    worm_renovacion_intentos    INTEGER NOT NULL DEFAULT 0,
    worm_renovacion_proximo_at  TIMESTAMPTZ,
    worm_renovacion_ultimo_error TEXT,
    worm_renovacion_agotado     BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (revocacion_id, artefacto)
);

COMMENT ON TABLE transferencia_acta_firma_excepcion_revocacion_worm IS
    'Estado del cableado WORM de los XML firmados de la REVOCACIÓN de una constancia de firma inobtenible (protección inicial y renovación CT, con backoff). Mutable por diseño.';

CREATE INDEX IF NOT EXISTS ix_firma_excepcion_revocacion_worm_pendiente
    ON transferencia_acta_firma_excepcion_revocacion_worm (worm_reconcile_proximo_at)
    WHERE worm_protegido_at IS NULL AND worm_reconcile_agotado = FALSE;
CREATE INDEX IF NOT EXISTS ix_firma_excepcion_revocacion_worm_renovacion_pendiente
    ON transferencia_acta_firma_excepcion_revocacion_worm (worm_renovacion_proximo_at)
    WHERE worm_protegido_at IS NOT NULL AND worm_renovacion_agotado = FALSE;

-- 6) Trigger de la constancia: conflicto de interés, parte que firmó y cadena -------------------
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
                      AND w.worm_protegido_at IS NOT NULL)) THEN
            RAISE EXCEPTION 'la revocación de la constancia sustituida no está sellada y protegida WORM'
                USING ERRCODE = '23514', CONSTRAINT = 'ck_firma_excepcion_revocacion_sin_proteger';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Rollback manual (no automatizado):
-- (restaurar firma_excepcion_guard_excepcion() de la 061)
-- DROP TABLE IF EXISTS transferencia_acta_firma_excepcion_revocacion_worm;
-- ALTER TABLE transferencia_acta_firma_excepcion_revocacion DROP CONSTRAINT IF EXISTS ck_firma_excepcion_revocacion_v2_completa;
-- ALTER TABLE transferencia_acta_firma_excepcion_revocacion DROP CONSTRAINT IF EXISTS ck_firma_excepcion_revocacion_sello_coherente;
-- ALTER TABLE transferencia_acta_firma_excepcion DROP CONSTRAINT IF EXISTS ck_firma_excepcion_v2_completa;
-- ALTER TABLE transferencia_acta_firma_excepcion DROP CONSTRAINT IF EXISTS ck_firma_excepcion_ordinal_coherente;
-- ALTER TABLE transferencia_acta_firma_excepcion DROP CONSTRAINT IF EXISTS uq_firma_excepcion_sustituye_a;
-- ALTER TABLE transferencia_acta_firma_excepcion DROP CONSTRAINT IF EXISTS uq_firma_excepcion_cadena;
-- (reponer UNIQUE (transferencia_id, rol) exige que no haya cadenas de más de un eslabón)
