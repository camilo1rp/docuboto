-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 061 (E12, AREA-07-10, remediación de auditoría): la constancia de «firma
-- inobtenible» pasa de texto libre a PRUEBA: firmada, protegida WORM y revocable.
-- search_path = tenant_{slug} (ADR-012).
--
-- Hallazgos que cierra:
--   * La constancia (059) no estaba firmada, no tenía Object-Lock y su soporte era un texto
--     libre: ante el AGN no probaba nada. Ahora lleva un XML canónico (huella del acta
--     congelada, parte, motivo, huella del fundamento, referencia, declarante, instante)
--     sellado con el sello institucional XAdES-T y, si el declarante tiene credencial, también
--     firmado por él (OBLIGATORIO si la política XAdES de la entidad lo exige para el nivel del
--     expediente). Las filas anteriores a esta migración quedan SIN firma (columnas NULL): se
--     DECLARAN así en el verificador, no se inventan.
--   * La referencia documental apunta a un radicado (y opcionalmente un anexo) EXISTENTE y
--     visible para el declarante, resuelto por el servidor contra document-service.
--   * Conflicto de interés: quien declara no puede ser NINGUNA de las partes de la
--     transferencia (enviada_por, decidida_por, creada_por), no solo la que no firma. Va en el
--     MOTOR (trigger) además del servicio.
--   * Acto «revocar excepción»: fila NUEVA append-only (nunca UPDATE) en
--     transferencia_acta_firma_excepcion_revocacion. El trigger de exclusión mutua
--     firma<->excepción solo considera excepciones NO revocadas: la parte puede volver a firmar.
--     Una excepción revocada NO se re-declara (UNIQUE (transferencia_id, rol) se conserva: la
--     constancia y su revocación son un par de evidencia cerrado y su artefacto WORM es
--     singleton por (expediente, tipo)).
--
-- Los triggers señalan su causa con CONSTRAINT = nombre estable para que el servicio distinga
-- el 409 (parte_ya_firmo / firma_excepcion_declarada) del 403 (conflicto de interés).
--
-- Idempotente: ADD COLUMN IF NOT EXISTS, CREATE TABLE IF NOT EXISTS, CREATE OR REPLACE FUNCTION,
-- DROP TRIGGER IF EXISTS y ADD CONSTRAINT guardado por pg_constraint.

-- 1) Soporte documental y firmas de la constancia ---------------------------------------------
ALTER TABLE transferencia_acta_firma_excepcion ADD COLUMN IF NOT EXISTS referencia_radicado_id UUID;
ALTER TABLE transferencia_acta_firma_excepcion ADD COLUMN IF NOT EXISTS referencia_anexo_id UUID;
ALTER TABLE transferencia_acta_firma_excepcion ADD COLUMN IF NOT EXISTS constancia_xml TEXT;
ALTER TABLE transferencia_acta_firma_excepcion ADD COLUMN IF NOT EXISTS constancia_sha256 TEXT;
ALTER TABLE transferencia_acta_firma_excepcion ADD COLUMN IF NOT EXISTS firma_id UUID;
ALTER TABLE transferencia_acta_firma_excepcion ADD COLUMN IF NOT EXISTS minio_key_firmado TEXT;
ALTER TABLE transferencia_acta_firma_excepcion ADD COLUMN IF NOT EXISTS xades_level TEXT;
ALTER TABLE transferencia_acta_firma_excepcion ADD COLUMN IF NOT EXISTS firma_personal_id UUID;
ALTER TABLE transferencia_acta_firma_excepcion ADD COLUMN IF NOT EXISTS minio_key_firmado_personal TEXT;
ALTER TABLE transferencia_acta_firma_excepcion ADD COLUMN IF NOT EXISTS xades_level_personal TEXT;
ALTER TABLE transferencia_acta_firma_excepcion
    ADD COLUMN IF NOT EXISTS firma_personal_requerida BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN transferencia_acta_firma_excepcion.constancia_xml IS
    'XML canónico que se firmó (urn:orpycamcp:acta-firma-excepcion:v1). NULL en constancias anteriores a la migración 061 (sin firma).';
COMMENT ON COLUMN transferencia_acta_firma_excepcion.firma_id IS
    'Sello institucional XAdES-T de la constancia (signature-service). NULL = constancia legada sin firmar.';
COMMENT ON COLUMN transferencia_acta_firma_excepcion.firma_personal_id IS
    'Firma personal XAdES del declarante, si tenía credencial activa (o la política de la entidad la exigía).';
COMMENT ON COLUMN transferencia_acta_firma_excepcion.firma_personal_requerida IS
    'Instantánea de la política XAdES de la entidad al declarar: TRUE = la firma personal del declarante era obligatoria.';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_firma_excepcion_sello_coherente'
                      AND conrelid = 'transferencia_acta_firma_excepcion'::regclass) THEN
        ALTER TABLE transferencia_acta_firma_excepcion
            ADD CONSTRAINT ck_firma_excepcion_sello_coherente
            CHECK ((firma_id IS NULL) = (minio_key_firmado IS NULL)
                   AND (firma_id IS NULL) = (constancia_xml IS NULL)
                   AND (firma_id IS NULL) = (constancia_sha256 IS NULL));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_firma_excepcion_firma_personal_coherente'
                      AND conrelid = 'transferencia_acta_firma_excepcion'::regclass) THEN
        ALTER TABLE transferencia_acta_firma_excepcion
            ADD CONSTRAINT ck_firma_excepcion_firma_personal_coherente
            CHECK ((firma_personal_id IS NULL) = (minio_key_firmado_personal IS NULL)
                   AND (NOT firma_personal_requerida OR firma_personal_id IS NOT NULL));
    END IF;
END;
$$;

-- 2) Revocación de la excepción: fila NUEVA, append-only ------------------------------------
CREATE TABLE IF NOT EXISTS transferencia_acta_firma_excepcion_revocacion (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    excepcion_id  UUID NOT NULL UNIQUE
                  REFERENCES transferencia_acta_firma_excepcion(id) ON DELETE CASCADE,
    revocada_por  UUID NOT NULL,
    fundamento    TEXT NOT NULL CHECK (length(btrim(fundamento)) >= 20),
    revocada_en   TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE transferencia_acta_firma_excepcion_revocacion IS
    'Revocación de una constancia de firma inobtenible (AREA-07-10): hecho POSTERIOR, append-only. La constancia original no se toca; con la revocación la parte vuelve a poder firmar.';

CREATE OR REPLACE FUNCTION reject_firma_excepcion_revocacion_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'transferencia_acta_firma_excepcion_revocacion is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_firma_excepcion_revocacion_immutable ON transferencia_acta_firma_excepcion_revocacion;
CREATE TRIGGER trg_firma_excepcion_revocacion_immutable
    BEFORE UPDATE OR DELETE ON transferencia_acta_firma_excepcion_revocacion
    FOR EACH ROW EXECUTE FUNCTION reject_firma_excepcion_revocacion_mutation();

CREATE OR REPLACE FUNCTION reject_firma_excepcion_revocacion_truncate() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'transferencia_acta_firma_excepcion_revocacion is append-only: TRUNCATE denegado';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_firma_excepcion_revocacion_no_truncate ON transferencia_acta_firma_excepcion_revocacion;
CREATE TRIGGER trg_firma_excepcion_revocacion_no_truncate
    BEFORE TRUNCATE ON transferencia_acta_firma_excepcion_revocacion
    FOR EACH STATEMENT EXECUTE FUNCTION reject_firma_excepcion_revocacion_truncate();

-- 3) Conflicto de interés y exclusión mutua en el MOTOR --------------------------------------
-- Excepción: ni el declarante puede ser parte de la transferencia, ni la parte que no firma
-- puede haber firmado ya. Mismo advisory lock por (transferencia, rol) que la firma personal.
CREATE OR REPLACE FUNCTION firma_excepcion_guard_excepcion() RETURNS trigger AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('acta_firma_rol'), hashtext(NEW.transferencia_id::text || ':' || NEW.rol));
    IF EXISTS (SELECT 1 FROM transferencias t
                WHERE t.id = NEW.transferencia_id
                  AND NEW.declarada_por::text IN (t.enviada_por, t.decidida_por, t.creada_por)) THEN
        RAISE EXCEPTION 'quien declara la excepción de firma no puede ser parte de la transferencia'
            USING ERRCODE = '23514', CONSTRAINT = 'ck_firma_excepcion_declarante_es_parte';
    END IF;
    IF EXISTS (SELECT 1 FROM transferencia_acta_firma_personal fp
                WHERE fp.transferencia_id = NEW.transferencia_id AND fp.rol = NEW.rol) THEN
        RAISE EXCEPTION 'el rol % del acta ya firmó personalmente: no admite excepción de firma', NEW.rol
            USING ERRCODE = '23514', CONSTRAINT = 'ck_firma_excepcion_parte_ya_firmo';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Firma personal: solo una excepción NO revocada la impide.
CREATE OR REPLACE FUNCTION firma_excepcion_guard_firma() RETURNS trigger AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('acta_firma_rol'), hashtext(NEW.transferencia_id::text || ':' || NEW.rol));
    IF EXISTS (SELECT 1 FROM transferencia_acta_firma_excepcion e
                WHERE e.transferencia_id = NEW.transferencia_id AND e.rol = NEW.rol
                  AND NOT EXISTS (SELECT 1 FROM transferencia_acta_firma_excepcion_revocacion r
                                   WHERE r.excepcion_id = e.id)) THEN
        RAISE EXCEPTION 'el rol % del acta tiene una excepción de firma declarada: no admite firma personal', NEW.rol
            USING ERRCODE = '23514', CONSTRAINT = 'ck_firma_personal_excepcion_vigente';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Revocación: quien revoca tampoco puede ser parte de la transferencia.
CREATE OR REPLACE FUNCTION firma_excepcion_revocacion_guard() RETURNS trigger AS $$
DECLARE
    v_transferencia UUID;
    v_rol TEXT;
BEGIN
    SELECT e.transferencia_id, e.rol INTO v_transferencia, v_rol
      FROM transferencia_acta_firma_excepcion e WHERE e.id = NEW.excepcion_id;
    PERFORM pg_advisory_xact_lock(hashtext('acta_firma_rol'), hashtext(v_transferencia::text || ':' || v_rol));
    IF EXISTS (SELECT 1 FROM transferencias t
                WHERE t.id = v_transferencia
                  AND NEW.revocada_por::text IN (t.enviada_por, t.decidida_por, t.creada_por)) THEN
        RAISE EXCEPTION 'quien revoca la excepción de firma no puede ser parte de la transferencia'
            USING ERRCODE = '23514', CONSTRAINT = 'ck_firma_excepcion_revocante_es_parte';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_firma_excepcion_revocacion_guard ON transferencia_acta_firma_excepcion_revocacion;
CREATE TRIGGER trg_firma_excepcion_revocacion_guard
    BEFORE INSERT ON transferencia_acta_firma_excepcion_revocacion
    FOR EACH ROW EXECUTE FUNCTION firma_excepcion_revocacion_guard();

-- 4) Object-Lock (WORM) de los artefactos de la constancia -----------------------------------
-- Tabla APARTE (mutable: intentos, backoff, confirmación) porque la constancia es append-only.
-- Dos artefactos por constancia: el XML sellado por la entidad ('sello') y el firmado por el
-- declarante ('personal'). Tipos en storage: acta_firma_excepcion_<rol> y
-- acta_firma_excepcion_personal_<rol> (migración 019 de storage-service).
CREATE TABLE IF NOT EXISTS transferencia_acta_firma_excepcion_worm (
    excepcion_id                UUID NOT NULL
                                REFERENCES transferencia_acta_firma_excepcion(id) ON DELETE CASCADE,
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
    PRIMARY KEY (excepcion_id, artefacto)
);

COMMENT ON TABLE transferencia_acta_firma_excepcion_worm IS
    'Estado del cableado WORM de los XML firmados de la constancia de firma inobtenible (AREA-07-10): protección inicial y renovación CT, con backoff. Mutable por diseño.';

CREATE INDEX IF NOT EXISTS ix_firma_excepcion_worm_pendiente
    ON transferencia_acta_firma_excepcion_worm (worm_reconcile_proximo_at)
    WHERE worm_protegido_at IS NULL AND worm_reconcile_agotado = FALSE;
CREATE INDEX IF NOT EXISTS ix_firma_excepcion_worm_renovacion_pendiente
    ON transferencia_acta_firma_excepcion_worm (worm_renovacion_proximo_at)
    WHERE worm_protegido_at IS NOT NULL AND worm_renovacion_agotado = FALSE;

-- Defensa en profundidad de privilegios (mismo guard de existencia de rol que la 054/059).
DO $$
DECLARE
    esquema TEXT := current_schema();
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'orpycamcp_app') THEN
        EXECUTE format('REVOKE UPDATE, DELETE ON TABLE %I.transferencia_acta_firma_excepcion_revocacion FROM orpycamcp_app', esquema);
    END IF;
END;
$$;

-- Rollback manual (no automatizado):
-- DROP TABLE IF EXISTS transferencia_acta_firma_excepcion_worm;
-- DROP TABLE IF EXISTS transferencia_acta_firma_excepcion_revocacion;
-- (y restaurar las funciones de la 059: firma_excepcion_guard_excepcion/firma_excepcion_guard_firma)
-- ALTER TABLE transferencia_acta_firma_excepcion DROP CONSTRAINT IF EXISTS ck_firma_excepcion_firma_personal_coherente;
-- ALTER TABLE transferencia_acta_firma_excepcion DROP CONSTRAINT IF EXISTS ck_firma_excepcion_sello_coherente;
