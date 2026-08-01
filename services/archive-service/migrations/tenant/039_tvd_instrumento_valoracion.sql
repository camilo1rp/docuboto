-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (ADR-026, Increment 1): la TVD entra como DISCRIMINADOR
-- sobre `trd_series`, no como tabla propia (ADR-026 D1: `expedientes.
-- trd_serie_id` es el seam único de nueve puntos de derivación, siete de
-- ellos alimentando `retain_until` WORM COMPLIANCE — irreversible).
--
-- INVARIANTE CENTRAL: el espacio de `code` es ÚNICO Y COMPARTIDO entre TRD y
-- TVD. Los índices de la 037 (`ux_trd_series_vigente`, `ux_trd_series_en_
-- tramite`, `UNIQUE(code,version)`) NO se tocan, y por eso toda resolución
-- por código sigue siendo correcta SIN filtro de tipo. Si algún día se
-- particiona ese espacio por tipo, hay que revisar `get_trd_serie_vigente_by_
-- code` (la llamada de `close_expediente` que escribe el pin WORM) ANTES.

-- ---------------------------------------------------------------------------
-- 1. Discriminador y contenido propio de la TVD
-- ---------------------------------------------------------------------------

-- DEFAULT 'TRD' rellena las filas EXISTENTES con un hecho VERIFICABLE (toda
-- fila anterior a esta migración es, por construcción, una TRD) y se QUEDA
-- como default permanente: los seeds y todo el código TRD existente siguen
-- funcionando sin tocarse. Contraste deliberado con la 037, donde el default
-- de `estado` SÍ tuvo que invertirse: allí el backfill habría afirmado un
-- hecho jurídico no verificado; aquí describe la forma de la fila.
ALTER TABLE trd_series
    ADD COLUMN IF NOT EXISTS tipo_instrumento         VARCHAR(3) NOT NULL DEFAULT 'TRD',
    ADD COLUMN IF NOT EXISTS fondo_nombre             TEXT,
    ADD COLUMN IF NOT EXISTS fecha_extrema_inicial    DATE,
    ADD COLUMN IF NOT EXISTS fecha_extrema_final      DATE,
    ADD COLUMN IF NOT EXISTS justificacion_valoracion TEXT;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema() AND t.relname = 'trd_series'
          AND c.conname = 'trd_series_tipo_instrumento_check') THEN
        ALTER TABLE trd_series ADD CONSTRAINT trd_series_tipo_instrumento_check
            CHECK (tipo_instrumento IN ('TRD','TVD'));
    END IF;

    -- Capa 3 (ADR-026 D2), dirección TVD: la TVD EXIGE su contenido propio y
    -- NO tiene fase de archivo de gestión (ya está en central/histórico).
    IF NOT EXISTS (SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema() AND t.relname = 'trd_series'
          AND c.conname = 'trd_series_tvd_forma_check') THEN
        ALTER TABLE trd_series ADD CONSTRAINT trd_series_tvd_forma_check
            CHECK (tipo_instrumento <> 'TVD' OR (
                fondo_nombre             IS NOT NULL AND
                justificacion_valoracion IS NOT NULL AND
                fecha_extrema_inicial    IS NOT NULL AND
                fecha_extrema_final      IS NOT NULL AND
                fecha_extrema_final     >= fecha_extrema_inicial AND
                COALESCE(archivo_gestion_years, 0) = 0));
    END IF;

    -- Capa 3, dirección TRD: una TRD no puede vestirse de TVD. Las columnas
    -- NULL de una TRD son una NEGACIÓN DECLARADA, no columnas huérfanas.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema() AND t.relname = 'trd_series'
          AND c.conname = 'trd_series_trd_forma_check') THEN
        ALTER TABLE trd_series ADD CONSTRAINT trd_series_trd_forma_check
            CHECK (tipo_instrumento <> 'TRD' OR (
                fondo_nombre             IS NULL AND
                justificacion_valoracion IS NULL AND
                fecha_extrema_inicial    IS NULL AND
                fecha_extrema_final      IS NULL));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_trd_series_tipo_code ON trd_series (tipo_instrumento, code);

-- ---------------------------------------------------------------------------
-- 2. Capa 2 — vistas: el aislamiento se mueve del WHERE (olvidable) al FROM
-- ---------------------------------------------------------------------------
-- DROP + CREATE, no `CREATE OR REPLACE`: con `SELECT *` un REPLACE falla en
-- cuanto una migración futura añada una columna a `trd_series`. Las vistas se
-- resuelven por search_path => son per-schema sin necesidad de guard.
DROP VIEW IF EXISTS v_trd_series;
CREATE VIEW v_trd_series AS
    SELECT * FROM trd_series WHERE tipo_instrumento = 'TRD'
    WITH CHECK OPTION;

DROP VIEW IF EXISTS v_tvd_agrupaciones;
CREATE VIEW v_tvd_agrupaciones AS
    SELECT * FROM trd_series WHERE tipo_instrumento = 'TVD'
    WITH CHECK OPTION;

-- ---------------------------------------------------------------------------
-- 3. Inmutabilidad: el tipo y el contenido valorado son append-only
-- ---------------------------------------------------------------------------
-- Extiende `reject_trd_version_mutation` (037/038). Dos reglas nuevas:
--  (a) `tipo_instrumento` es inmutable en TODO estado, incluido `borrador`:
--      un borrador que cambia de instrumento invalidaría el pin de cualquier
--      expediente y el criterio de las vistas. Un cambio de tipo es una fila
--      nueva, no una edición.
--  (b) las cuatro columnas de contenido TVD entran en la lista de campos
--      sustantivos inmutables fuera de `borrador`, junto a retención y
--      disposición: son el FUNDAMENTO de la eliminación (D5).
-- El resto del cuerpo (guardias `convalidada` de la 038) se conserva
-- íntegro: NO se está reemplazando esa lógica, se está EXTENDIENDO.
CREATE OR REPLACE FUNCTION reject_trd_version_mutation() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.estado <> 'borrador' THEN
            RAISE EXCEPTION 'trd_series: una version % del codigo % en estado % es append-only (Ac. AGN 001/2024 -- compila 004/2019; ADR-025)',
                OLD.version, OLD.code, OLD.estado;
        END IF;
        RETURN OLD;
    END IF;

    -- (a) ADR-026: inmutable SIEMPRE, sin excepcion de estado.
    IF NEW.tipo_instrumento IS DISTINCT FROM OLD.tipo_instrumento THEN
        RAISE EXCEPTION 'trd_series: `tipo_instrumento` es inmutable (codigo %, version %) — TRD y TVD no se convierten entre si (ADR-026)',
            OLD.code, OLD.version;
    END IF;

    IF OLD.estado <> 'borrador' THEN
        -- `pdfa_profile`, `is_active`, `estado` y los campos del acto quedan
        -- FUERA de este bloque a propósito: `pdfa_profile` es politica de
        -- preservacion tecnica (E10/migracion 035); `is_active`/`estado` los
        -- gobierna el bloque de mas abajo; los campos del acto, el siguiente.
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
        OR NEW.valid_from            IS DISTINCT FROM OLD.valid_from
        -- (b) ADR-026: contenido valorado de la TVD.
        OR NEW.fondo_nombre             IS DISTINCT FROM OLD.fondo_nombre
        OR NEW.justificacion_valoracion IS DISTINCT FROM OLD.justificacion_valoracion
        OR NEW.fecha_extrema_inicial    IS DISTINCT FROM OLD.fecha_extrema_inicial
        OR NEW.fecha_extrema_final      IS DISTINCT FROM OLD.fecha_extrema_final THEN
            RAISE EXCEPTION 'trd_series: contenido inmutable en estado % (codigo %, version %) — una modificacion sustantiva crea una version nueva (ADR-025)',
                OLD.estado, OLD.code, OLD.version;
        END IF;
    END IF;

    -- (038) desde `convalidada` el unico destino de `estado` es `derogada`;
    -- acto/fecha/instancia inmutables; rusd/publicacion fill-once.
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
-- El trigger `trg_trd_series_append_only` (037) ya apunta a esta funcion:
-- CREATE OR REPLACE FUNCTION basta, no hay que recrear el trigger.

-- ---------------------------------------------------------------------------
-- 4. Coherencia de la jerarquia: un padre no cruza de instrumento
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION assert_trd_parent_mismo_tipo() RETURNS trigger AS $$
DECLARE
    parent_tipo VARCHAR(3);
BEGIN
    IF NEW.parent_id IS NOT NULL THEN
        SELECT tipo_instrumento INTO parent_tipo FROM trd_series WHERE id = NEW.parent_id;
        IF parent_tipo IS DISTINCT FROM NEW.tipo_instrumento THEN
            RAISE EXCEPTION 'trd_series: el padre % es de tipo % y la fila es de tipo % — la jerarquia no cruza instrumentos (ADR-026 D2)',
                NEW.parent_id, COALESCE(parent_tipo,'(inexistente)'), NEW.tipo_instrumento;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_trd_series_parent_tipo ON trd_series;
CREATE TRIGGER trg_trd_series_parent_tipo
    BEFORE INSERT OR UPDATE ON trd_series
    FOR EACH ROW EXECUTE FUNCTION assert_trd_parent_mismo_tipo();

-- ---------------------------------------------------------------------------
-- 5. Capa 4 — el expediente no se clasifica bajo el instrumento equivocado
-- ---------------------------------------------------------------------------
-- Forma ESTRICTA del Increment 1: `origen` todavia no existe, asi que NINGUN
-- expediente puede apuntar a una TVD. Consecuencia buscada: el Increment 1
-- registra y convalida TVD con riesgo WORM CERO, garantizado por el motor —
-- ninguna ruta (incluido `batch_service`) puede colar una TVD en el calculo
-- de retencion antes de que exista `compute_retention` con fecha base
-- correcta (ADR-026 D4). El Increment 2 RELAJA este trigger a la regla
-- `origen <-> tipo_instrumento`; no lo elimina.
CREATE OR REPLACE FUNCTION assert_expediente_pin_tipo_instrumento() RETURNS trigger AS $$
DECLARE
    pin_tipo VARCHAR(3);
BEGIN
    IF NEW.trd_serie_id IS NOT NULL
       AND (TG_OP = 'INSERT' OR NEW.trd_serie_id IS DISTINCT FROM OLD.trd_serie_id) THEN
        SELECT tipo_instrumento INTO pin_tipo FROM trd_series WHERE id = NEW.trd_serie_id;
        IF pin_tipo = 'TVD' THEN
            RAISE EXCEPTION 'expedientes: la agrupacion TVD % no puede gobernar un expediente todavia — el amarre de fondo acumulado llega en el Increment 2 (ADR-026 D3/D4)',
                NEW.trd_serie_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_expedientes_pin_tipo ON expedientes;
CREATE TRIGGER trg_expedientes_pin_tipo
    BEFORE INSERT OR UPDATE ON expedientes
    FOR EACH ROW EXECUTE FUNCTION assert_expediente_pin_tipo_instrumento();

-- Rollback (referencia manual, no ejecutado por el runner):
--   DROP TRIGGER IF EXISTS trg_expedientes_pin_tipo ON expedientes;
--   DROP TRIGGER IF EXISTS trg_trd_series_parent_tipo ON trd_series;
--   DROP VIEW IF EXISTS v_trd_series, v_tvd_agrupaciones;
--   ALTER TABLE trd_series DROP CONSTRAINT IF EXISTS trd_series_tvd_forma_check;
--   ALTER TABLE trd_series DROP CONSTRAINT IF EXISTS trd_series_trd_forma_check;
--   ALTER TABLE trd_series DROP CONSTRAINT IF EXISTS trd_series_tipo_instrumento_check;
--   (restaurar `reject_trd_version_mutation` al cuerpo instalado por la 038)
--   (el rollback NO puede ejecutarse si ya existe alguna fila tipo_instrumento='TVD')
