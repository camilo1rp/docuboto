-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 059 (E12, AREA-07-10): acto «firma inobtenible» del acta de entrega.
-- search_path = tenant_{slug} (ADR-012).
--
-- Cuando una PARTE del acta (elabora / entrega / recibe) no puede firmar —fallecimiento, retiro,
-- incapacidad— el acta quedaba para siempre sin firmar por esa parte. Un acto administrativo
-- explícito lo declara: queda como CONSTANCIA inmutable (esta tabla) y asiento atómico en
-- public.audit_log. El acta NO pasa a firmada_completa: el verificador la reporta
-- «firmada_con_excepcion». El acta congelada (transferencia_acta) NO se toca: es probatoria y su
-- huella está anclada; la excepción es un hecho POSTERIOR y vive aparte.
--
-- Garantías en el MOTOR (no solo en el servicio):
--   * UNIQUE (transferencia_id, rol): una excepción por parte.
--   * CHECK declarada_por <> parte_id: quien declara no es la parte que no firma.
--   * fundamento y referencia_documental obligatorios (no vacíos), motivo de catálogo cerrado.
--   * Exclusión mutua firma personal <-> excepción para el MISMO rol, en ambos sentidos, con
--     advisory lock transaccional por (transferencia, rol) para cerrar la carrera.
--   * Append-only (UPDATE/DELETE/TRUNCATE rechazados) y, si existe el rol de la app, sin
--     UPDATE/DELETE por privilegios.
--
-- Idempotente: CREATE TABLE IF NOT EXISTS + CREATE OR REPLACE FUNCTION + DROP TRIGGER IF EXISTS.

CREATE TABLE IF NOT EXISTS transferencia_acta_firma_excepcion (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transferencia_id      UUID NOT NULL REFERENCES transferencias(id) ON DELETE CASCADE,
    rol                   TEXT NOT NULL CHECK (rol IN ('recibe', 'entrega', 'elabora')),
    parte_id              UUID NOT NULL,
    motivo                TEXT NOT NULL CHECK (motivo IN ('fallecimiento', 'retiro', 'incapacidad', 'otro')),
    fundamento            TEXT NOT NULL CHECK (length(btrim(fundamento)) >= 20),
    referencia_documental TEXT NOT NULL CHECK (length(btrim(referencia_documental)) >= 3),
    declarada_por         UUID NOT NULL,
    declarada_en          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (transferencia_id, rol),
    CONSTRAINT ck_firma_excepcion_declarante_no_es_la_parte CHECK (declarada_por <> parte_id)
);

COMMENT ON TABLE transferencia_acta_firma_excepcion IS
    'Constancia inmutable de que una parte del acta de entrega NO PUDO firmar (acto administrativo, AREA-07-10). El acta pasa a firmada_con_excepcion, nunca a firmada_completa.';
COMMENT ON COLUMN transferencia_acta_firma_excepcion.parte_id IS
    'Identidad (auth_users.id) de la parte que no firma, DERIVADA por el servidor de enviada_por/decidida_por/creada_por según rol; nunca un dato del cliente.';
COMMENT ON COLUMN transferencia_acta_firma_excepcion.declarada_por IS
    'Quien declara la excepción (USUA_PERM_ADMIN crud>=3). Distinto de parte_id por CHECK.';

CREATE INDEX IF NOT EXISTS ix_transferencia_acta_firma_excepcion_transferencia
    ON transferencia_acta_firma_excepcion (transferencia_id);

CREATE OR REPLACE FUNCTION reject_firma_excepcion_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'transferencia_acta_firma_excepcion is append-only (constancia de excepción de firma inmutable)';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_firma_excepcion_immutable ON transferencia_acta_firma_excepcion;
CREATE TRIGGER trg_firma_excepcion_immutable
    BEFORE UPDATE OR DELETE ON transferencia_acta_firma_excepcion
    FOR EACH ROW EXECUTE FUNCTION reject_firma_excepcion_mutation();

CREATE OR REPLACE FUNCTION reject_firma_excepcion_truncate() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'transferencia_acta_firma_excepcion is append-only: TRUNCATE denegado';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_firma_excepcion_no_truncate ON transferencia_acta_firma_excepcion;
CREATE TRIGGER trg_firma_excepcion_no_truncate
    BEFORE TRUNCATE ON transferencia_acta_firma_excepcion
    FOR EACH STATEMENT EXECUTE FUNCTION reject_firma_excepcion_truncate();

-- Exclusión mutua firma <-> excepción (mismo rol). El advisory lock serializa a los dos
-- escritores concurrentes de una misma (transferencia, rol): sin él, bajo READ COMMITTED
-- ambos podrían pasar su comprobación y escribir.
CREATE OR REPLACE FUNCTION firma_excepcion_guard_excepcion() RETURNS trigger AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('acta_firma_rol'), hashtext(NEW.transferencia_id::text || ':' || NEW.rol));
    IF EXISTS (SELECT 1 FROM transferencia_acta_firma_personal fp
                WHERE fp.transferencia_id = NEW.transferencia_id AND fp.rol = NEW.rol) THEN
        RAISE EXCEPTION 'el rol % del acta ya firmó personalmente: no admite excepción de firma', NEW.rol
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_firma_excepcion_guard ON transferencia_acta_firma_excepcion;
CREATE TRIGGER trg_firma_excepcion_guard
    BEFORE INSERT ON transferencia_acta_firma_excepcion
    FOR EACH ROW EXECUTE FUNCTION firma_excepcion_guard_excepcion();

CREATE OR REPLACE FUNCTION firma_excepcion_guard_firma() RETURNS trigger AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('acta_firma_rol'), hashtext(NEW.transferencia_id::text || ':' || NEW.rol));
    IF EXISTS (SELECT 1 FROM transferencia_acta_firma_excepcion e
                WHERE e.transferencia_id = NEW.transferencia_id AND e.rol = NEW.rol) THEN
        RAISE EXCEPTION 'el rol % del acta tiene una excepción de firma declarada: no admite firma personal', NEW.rol
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_firma_personal_excepcion_guard ON transferencia_acta_firma_personal;
CREATE TRIGGER trg_firma_personal_excepcion_guard
    BEFORE INSERT ON transferencia_acta_firma_personal
    FOR EACH ROW EXECUTE FUNCTION firma_excepcion_guard_firma();

-- Defensa en profundidad de privilegios (mismo guard de existencia de rol que la 054).
DO $$
DECLARE
    esquema TEXT := current_schema();
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'orpycamcp_app') THEN
        EXECUTE format('REVOKE UPDATE, DELETE ON TABLE %I.transferencia_acta_firma_excepcion FROM orpycamcp_app', esquema);
    END IF;
END;
$$;

-- Rollback manual (no automatizado):
-- DROP TRIGGER IF EXISTS trg_firma_personal_excepcion_guard ON transferencia_acta_firma_personal;
-- DROP FUNCTION IF EXISTS firma_excepcion_guard_firma();
-- DROP TABLE IF EXISTS transferencia_acta_firma_excepcion;
-- DROP FUNCTION IF EXISTS firma_excepcion_guard_excepcion();
-- DROP FUNCTION IF EXISTS reject_firma_excepcion_mutation();
-- DROP FUNCTION IF EXISTS reject_firma_excepcion_truncate();
