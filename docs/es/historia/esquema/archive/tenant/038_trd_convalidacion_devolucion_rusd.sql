-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (ADR-025, Increment 2 -- remediación del dictamen de
-- `archival-compliance-auditor` sobre el Increment 2): cierra el hallazgo
-- [Alta] #1 (aprobada era un callejón sin salida: sin DELETE, sin derogar,
-- sin PATCH -- la única puerta era declarar una convalidación falsa), añade
-- la evidencia del PRIMER acto del circuito (hallazgo #5: acta del Comité,
-- hoy sin ningún camino de código que la escriba) y blinda a nivel de motor
-- el fundamento jurídico de una versión ya `convalidada` (hallazgo de
-- seguridad: hoy solo dos capas de aplicación lo protegen).

-- ---------------------------------------------------------------------------
-- 1. Evidencia del acto del Comité (hallazgo #5): `acta_comite` faltaba --
--    `fecha_aprobacion_comite` ya existía desde la 037 pero NINGÚN camino de
--    código la escribía (columna vestigial, igual que `version`/`valid_from`
--    lo eran antes de esta misma ADR).
-- ---------------------------------------------------------------------------
ALTER TABLE trd_series
    ADD COLUMN IF NOT EXISTS acta_comite TEXT;

-- ---------------------------------------------------------------------------
-- 2. Trigger de inmutabilidad (037): se EXTIENDE, no se sustituye. Dos
--    guardias nuevas, ambas dentro de la MISMA función (`CREATE OR REPLACE`,
--    ya idempotente por convención de este archivo) para no duplicar el
--    trigger que la dispara:
--
--    (a) Una vez `convalidada`, el ÚNICO destino de `estado` es `derogada`
--        (cierra el hallazgo de seguridad "el motor no protege el
--        fundamento jurídico" -- hoy dependía al 100% de dos capas de
--        aplicación).
--    (b) El acto de convalidación (`acto_administrativo`,
--        `fecha_convalidacion`, `instancia_convalidante`) es inmutable desde
--        `convalidada`. `rusd_radicado`/`fecha_publicacion` (hallazgo #4:
--        el RUSD y la publicación llegan HASTA 30 días hábiles DESPUÉS de
--        la convalidación, Ac. AGN 001/2024 que compila 004/2019) se
--        permiten rellenar de NULL a un valor -- pero, una vez puestos,
--        tampoco se pueden reescribir. Es el matiz que pidió el auditor de
--        seguridad: NULL -> valor pasa, valor -> otro valor no.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION reject_trd_version_mutation() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.estado <> 'borrador' THEN
            RAISE EXCEPTION 'trd_series: una version % del codigo % en estado % es append-only (Ac. AGN 001/2024 -- compila 004/2019; ADR-025)',
                OLD.version, OLD.code, OLD.estado;
        END IF;
        RETURN OLD;
    END IF;

    IF OLD.estado <> 'borrador' THEN
        -- `pdfa_profile`, `is_active`, `estado` y los campos del acto quedan
        -- FUERA de este bloque a propósito: `pdfa_profile` es politica de
        -- preservacion tecnica (E10/migracion 035); `is_active`/`estado` los
        -- gobierna el bloque (a) de abajo; los campos del acto, el (b).
        IF NEW.code                  IS DISTINCT FROM OLD.code
        OR NEW.name                  IS DISTINCT FROM OLD.name
        OR NEW.description           IS DISTINCT FROM OLD.description
        OR NEW.parent_id             IS DISTINCT FROM OLD.parent_id
        OR NEW.retention_years       IS DISTINCT FROM OLD.retention_years
        OR NEW.total_retention       IS DISTINCT FROM OLD.total_retention
        OR NEW.archivo_gestion_years IS DISTINCT FROM OLD.archivo_gestion_years
        OR NEW.archivo_central_years IS DISTINCT FROM OLD.archivo_central_years
        OR NEW.disposition           IS DISTINCT FROM OLD.disposition
        OR NEW.version               IS DISTINCT FROM OLD.version
        OR NEW.valid_from            IS DISTINCT FROM OLD.valid_from THEN
            RAISE EXCEPTION 'trd_series: contenido inmutable en estado % (codigo %, version %) — una modificacion sustantiva crea una version nueva (ADR-025)',
                OLD.estado, OLD.code, OLD.version;
        END IF;
    END IF;

    -- (a)+(b) -- ADR-025/038: el fundamento juridico de una version YA
    -- convalidada no lo sostiene solo la aplicacion.
    IF OLD.estado = 'convalidada' THEN
        IF NEW.estado IS DISTINCT FROM OLD.estado AND NEW.estado <> 'derogada' THEN
            RAISE EXCEPTION 'trd_series: desde convalidada la unica transicion valida es a derogada (codigo %, version %; ADR-025/038)',
                OLD.code, OLD.version;
        END IF;
        IF NEW.acto_administrativo    IS DISTINCT FROM OLD.acto_administrativo
        OR NEW.fecha_convalidacion    IS DISTINCT FROM OLD.fecha_convalidacion
        OR NEW.instancia_convalidante IS DISTINCT FROM OLD.instancia_convalidante THEN
            RAISE EXCEPTION 'trd_series: el acto de convalidacion es inmutable (codigo %, version %; ADR-025/038)',
                OLD.code, OLD.version;
        END IF;
        IF OLD.rusd_radicado IS NOT NULL AND NEW.rusd_radicado IS DISTINCT FROM OLD.rusd_radicado THEN
            RAISE EXCEPTION 'trd_series: rusd_radicado ya registrado es inmutable (codigo %, version %; ADR-025/038)',
                OLD.code, OLD.version;
        END IF;
        IF OLD.fecha_publicacion IS NOT NULL AND NEW.fecha_publicacion IS DISTINCT FROM OLD.fecha_publicacion THEN
            RAISE EXCEPTION 'trd_series: fecha_publicacion ya registrada es inmutable (codigo %, version %; ADR-025/038)',
                OLD.code, OLD.version;
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- El trigger YA existe (037) y apunta a esta funcion por nombre -- un
-- `CREATE OR REPLACE FUNCTION` basta, no hace falta recrear el trigger. Se
-- deja el DROP/CREATE de todas formas por el mismo criterio defensivo que ya
-- usa este archivo en otras migraciones (idempotencia explicita, no implicita).
DROP TRIGGER IF EXISTS trg_trd_series_append_only ON trd_series;
CREATE TRIGGER trg_trd_series_append_only
    BEFORE UPDATE OR DELETE ON trd_series
    FOR EACH ROW EXECUTE FUNCTION reject_trd_version_mutation();

-- Rollback (referencia manual, no ejecutado por el runner):
--   ALTER TABLE trd_series DROP COLUMN IF EXISTS acta_comite;
--   (recrear la funcion `reject_trd_version_mutation` en su version 037 restaura
--    el comportamiento anterior de las guardias (a)/(b) de arriba)
