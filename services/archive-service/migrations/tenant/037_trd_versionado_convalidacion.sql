-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (ADR-025, Increment 1): la TRD pasa de tabla de
-- configuración a instrumento convalidado. Cierra la fuga retroactiva: hoy
-- `UPDATE trd_series` altera el calendario de disposición de expedientes
-- cerrados hace años y el `retain_until` WORM COMPLIANCE (irreversible) de
-- índices, actas y AIPs.

-- ---------------------------------------------------------------------------
-- 1. trd_series: coexistencia de versiones
-- ---------------------------------------------------------------------------

-- UNIQUE(code) creada inline en 001 => nombre autogenerado `trd_series_code_key`.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint c
          JOIN pg_class t     ON t.oid = c.conrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE n.nspname = current_schema()
           AND t.relname = 'trd_series'
           AND c.conname = 'trd_series_code_key'
    ) THEN
        ALTER TABLE trd_series DROP CONSTRAINT trd_series_code_key;
    END IF;
END $$;

-- `ADD COLUMN IF NOT EXISTS` es nativamente schema-scoped (no necesita guard).
-- DEFAULT 'migrada' rellena las filas EXISTENTES con la única verdad
-- verificable (D6); el DEFAULT para filas NUEVAS pasa a 'borrador' justo
-- después. El orden importa.
ALTER TABLE trd_series
    ADD COLUMN IF NOT EXISTS estado                  VARCHAR(20) NOT NULL DEFAULT 'migrada',
    ADD COLUMN IF NOT EXISTS valid_to                DATE,
    ADD COLUMN IF NOT EXISTS acto_administrativo     TEXT,
    ADD COLUMN IF NOT EXISTS fecha_aprobacion_comite DATE,
    ADD COLUMN IF NOT EXISTS fecha_convalidacion     DATE,
    ADD COLUMN IF NOT EXISTS instancia_convalidante  TEXT,
    ADD COLUMN IF NOT EXISTS rusd_radicado           TEXT,
    ADD COLUMN IF NOT EXISTS fecha_publicacion       DATE,
    ADD COLUMN IF NOT EXISTS supersede_a             UUID REFERENCES trd_series(id),
    ADD COLUMN IF NOT EXISTS created_by              UUID;

ALTER TABLE trd_series ALTER COLUMN estado SET DEFAULT 'borrador';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema() AND t.relname = 'trd_series'
          AND c.conname = 'trd_series_estado_check') THEN
        ALTER TABLE trd_series ADD CONSTRAINT trd_series_estado_check
            CHECK (estado IN ('borrador','aprobada','convalidada','migrada','derogada'));
    END IF;

    -- Convalidada EXIGE el acto (Ac. AGN 004/2019).
    IF NOT EXISTS (SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema() AND t.relname = 'trd_series'
          AND c.conname = 'trd_series_convalidada_acto_check') THEN
        ALTER TABLE trd_series ADD CONSTRAINT trd_series_convalidada_acto_check
            CHECK (estado <> 'convalidada' OR (
                acto_administrativo    IS NOT NULL AND
                fecha_convalidacion    IS NOT NULL AND
                instancia_convalidante IS NOT NULL));
    END IF;

    -- D6: `migrada` NO PUEDE llevar datos de acto. El motor impide que una
    -- serie migrada se disfrace de convalidada rellenando campos.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema() AND t.relname = 'trd_series'
          AND c.conname = 'trd_series_migrada_sin_acto_check') THEN
        ALTER TABLE trd_series ADD CONSTRAINT trd_series_migrada_sin_acto_check
            CHECK (estado <> 'migrada' OR (
                acto_administrativo    IS NULL AND
                fecha_convalidacion    IS NULL AND
                instancia_convalidante IS NULL AND
                rusd_radicado          IS NULL AND
                fecha_aprobacion_comite IS NULL));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema() AND t.relname = 'trd_series'
          AND c.conname = 'trd_series_vigencia_check') THEN
        ALTER TABLE trd_series ADD CONSTRAINT trd_series_vigencia_check
            CHECK ((valid_to IS NULL OR valid_to >= valid_from)
               AND (estado <> 'derogada' OR valid_to IS NOT NULL)
               AND version >= 1);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema() AND t.relname = 'trd_series'
          AND c.conname = 'trd_series_code_version_key') THEN
        ALTER TABLE trd_series ADD CONSTRAINT trd_series_code_version_key UNIQUE (code, version);
    END IF;
END $$;

-- Una sola versión VIGENTE por código (los nombres de índice son per-schema:
-- `IF NOT EXISTS` resuelve contra current_schema()).
CREATE UNIQUE INDEX IF NOT EXISTS ux_trd_series_vigente
    ON trd_series (code)
 WHERE estado IN ('convalidada','migrada') AND valid_to IS NULL;

-- Una sola versión EN TRÁMITE por código (evita bifurcar el instrumento).
CREATE UNIQUE INDEX IF NOT EXISTS ux_trd_series_en_tramite
    ON trd_series (code)
 WHERE estado IN ('borrador','aprobada');

CREATE INDEX IF NOT EXISTS ix_trd_series_code_version ON trd_series (code, version DESC);

-- ---------------------------------------------------------------------------
-- 2. Inmutabilidad ESTRUCTURAL de las versiones no-borrador (precedente: 023)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION reject_trd_version_mutation() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.estado <> 'borrador' THEN
            RAISE EXCEPTION 'trd_series: una version % del codigo % en estado % es append-only (Ac. AGN 004/2019; ADR-025)',
                OLD.version, OLD.code, OLD.estado;
        END IF;
        RETURN OLD;
    END IF;

    IF OLD.estado <> 'borrador' THEN
        -- `pdfa_profile` y `is_active` quedan FUERA: no son contenido
        -- convalidado de la TRD (pdfa_profile es politica de preservacion,
        -- E10/migracion 035; is_active lo mueve la derogacion).
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
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_trd_series_append_only ON trd_series;
CREATE TRIGGER trg_trd_series_append_only
    BEFORE UPDATE OR DELETE ON trd_series
    FOR EACH ROW EXECUTE FUNCTION reject_trd_version_mutation();

CREATE OR REPLACE FUNCTION reject_trd_series_truncate() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'trd_series es append-only: TRUNCATE denegado (ADR-025)';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_trd_series_no_truncate ON trd_series;
CREATE TRIGGER trg_trd_series_no_truncate
    BEFORE TRUNCATE ON trd_series
    FOR EACH STATEMENT EXECUTE FUNCTION reject_trd_series_truncate();

-- ---------------------------------------------------------------------------
-- 3. expedientes: amarre a la version + marca de revision
-- ---------------------------------------------------------------------------
ALTER TABLE expedientes
    ADD COLUMN IF NOT EXISTS trd_serie_code         VARCHAR(20),
    ADD COLUMN IF NOT EXISTS trd_revision_requerida BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS trd_revision_motivo    TEXT;

CREATE INDEX IF NOT EXISTS ix_expedientes_trd_serie_code ON expedientes (trd_serie_code);
CREATE INDEX IF NOT EXISTS ix_expedientes_trd_revision
    ON expedientes (trd_revision_requerida) WHERE trd_revision_requerida;

-- Backfill del codigo desde la fila actual (que sera la version 1).
UPDATE expedientes e
   SET trd_serie_code = s.code
  FROM trd_series s
 WHERE s.id = e.trd_serie_id
   AND e.trd_serie_code IS NULL;

-- FK REAL a la fila-version (hoy `trd_serie_id` es un UUID suelto, sin FK:
-- nada impide que la version que gobierna un expediente desaparezca).
-- NOT VALID: no se puede fallar la provision por huerfanos historicos; las
-- escrituras NUEVAS si quedan verificadas desde ya. La validacion completa
-- (`VALIDATE CONSTRAINT`) va en el Increment 2, tras depurar huerfanos.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema() AND t.relname = 'expedientes'
          AND c.conname = 'expedientes_trd_serie_id_fkey') THEN
        ALTER TABLE expedientes
            ADD CONSTRAINT expedientes_trd_serie_id_fkey
            FOREIGN KEY (trd_serie_id) REFERENCES trd_series(id) NOT VALID;
    END IF;
END $$;

-- Marcas de revision (D4/D6). NO se inventa ningun calendario.
UPDATE expedientes
   SET trd_revision_requerida = true,
       trd_revision_motivo    = 'cierre_sin_snapshot_de_retencion'
 WHERE status IN ('closed','transferred')
   AND disposition IS NULL
   AND NOT trd_revision_requerida;

UPDATE expedientes
   SET trd_revision_requerida = true,
       trd_revision_motivo    = 'serie_no_resoluble'
 WHERE trd_serie_id IS NOT NULL
   AND trd_serie_code IS NULL
   AND NOT trd_revision_requerida;

UPDATE expedientes
   SET trd_revision_requerida = true,
       trd_revision_motivo    = 'cerrado_bajo_serie_migrada'
 WHERE status IN ('closed','transferred')
   AND trd_serie_code IS NOT NULL
   AND disposition IS NOT NULL
   AND NOT trd_revision_requerida;

-- Congelacion del amarre: tras el cierre, `trd_serie_id` no se reescribe nunca.
-- (Al CERRAR, OLD.status aun es 'open' -> la escritura de congelacion pasa.)
CREATE OR REPLACE FUNCTION reject_trd_pin_mutation() RETURNS trigger AS $$
BEGIN
    IF OLD.status IN ('closed','transferred')
       AND NEW.trd_serie_id IS DISTINCT FROM OLD.trd_serie_id THEN
        RAISE EXCEPTION 'expedientes: la version TRD que gobierna un expediente % es inmutable tras el cierre (ADR-025)', OLD.status;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_expedientes_trd_pin_immutable ON expedientes;
CREATE TRIGGER trg_expedientes_trd_pin_immutable
    BEFORE UPDATE ON expedientes
    FOR EACH ROW EXECUTE FUNCTION reject_trd_pin_mutation();

-- Rollback (referencia manual, no ejecutado por el runner):
--   DROP TRIGGER IF EXISTS trg_expedientes_trd_pin_immutable ON expedientes;
--   DROP TRIGGER IF EXISTS trg_trd_series_append_only ON trd_series;
--   DROP TRIGGER IF EXISTS trg_trd_series_no_truncate ON trd_series;
--   DROP INDEX IF EXISTS ux_trd_series_vigente, ux_trd_series_en_tramite;
--   ALTER TABLE trd_series DROP CONSTRAINT IF EXISTS trd_series_code_version_key; ...
--   (el rollback NO restaura UNIQUE(code) si ya existen varias versiones)
