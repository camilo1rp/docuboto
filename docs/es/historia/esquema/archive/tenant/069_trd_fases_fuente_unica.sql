-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant 069 (C-4 / C6, ADR-040 + addenda ADR-015/025/026).
-- search_path = tenant_{slug} (ADR-012).
--
-- La retención de una serie tiene UNA fuente: las dos fases (`archivo_gestion_
-- years` / `archivo_central_years`). `retention_years` y `total_retention` eran
-- el juego legado: dos nombres para un mismo hecho, que podían quedar en
-- desacuerdo (una TVD creada sin fase central mostraba 10 años y calendarizaba
-- 0). Se ELIMINAN de la tabla; la API los sigue DERIVANDO en lectura
-- (`retention_years` = gestión, `total_retention` = gestión + central).
--
--   1. Se sueltan las dos vistas (`SELECT *` depende de las columnas).
--   2. `reject_trd_version_mutation` pierde las dos líneas del juego legado.
--   3. DROP COLUMN retention_years, total_retention.
--   4. CHECK `trd_series_fases_check`: toda TRD declara AMBAS fases y toda TVD
--      su fase central. Sin default: un 0 silencioso es retención cero. Y
--      `trd_series_retencion_minima_check`: una TRD suma al menos un año.
--   5. `assert_trd_parent_mismo_tipo` impone el tope de DOS niveles
--      (serie > subserie): el padre debe ser una serie raíz y una fila con
--      hijos no puede ganar padre.
--   6. Las vistas se recrean con WITH CHECK OPTION.
--
-- Sin backfill (datos de prueba): si alguna fila viola el CHECK, la migración
-- aborta listando las filas; el entorno se repara o se recrea.
-- Idempotente: IF EXISTS / OR REPLACE / guardas contra pg_constraint.

-- 1. vistas ---------------------------------------------------------------
DROP VIEW IF EXISTS v_trd_series;
DROP VIEW IF EXISTS v_tvd_agrupaciones;

-- 2. trigger de inmutabilidad, SIN el juego legado ---------------------------
-- Cuerpo de la 039 con dos líneas menos (`retention_years`, `total_retention`):
-- referenciarlas tras el DROP COLUMN reventaría el primer UPDATE.
CREATE OR REPLACE FUNCTION reject_trd_version_mutation() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.estado <> 'borrador' THEN
            RAISE EXCEPTION 'trd_series: una version % del codigo % en estado % es append-only (Ac. AGN 001/2024 -- compila 004/2019; ADR-025)',
                OLD.version, OLD.code, OLD.estado;
        END IF;
        RETURN OLD;
    END IF;

    -- ADR-026: inmutable SIEMPRE, sin excepcion de estado.
    IF NEW.tipo_instrumento IS DISTINCT FROM OLD.tipo_instrumento THEN
        RAISE EXCEPTION 'trd_series: `tipo_instrumento` es inmutable (codigo %, version %) — TRD y TVD no se convierten entre si (ADR-026)',
            OLD.code, OLD.version;
    END IF;

    IF OLD.estado <> 'borrador' THEN
        IF NEW.code                  IS DISTINCT FROM OLD.code
        OR NEW.name                  IS DISTINCT FROM OLD.name
        OR NEW.description           IS DISTINCT FROM OLD.description
        OR NEW.parent_id             IS DISTINCT FROM OLD.parent_id
        OR NEW.archivo_gestion_years IS DISTINCT FROM OLD.archivo_gestion_years
        OR NEW.archivo_central_years IS DISTINCT FROM OLD.archivo_central_years
        OR NEW.disposition           IS DISTINCT FROM OLD.disposition
        OR NEW.version               IS DISTINCT FROM OLD.version
        OR NEW.valid_from            IS DISTINCT FROM OLD.valid_from
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

-- 3 y 4. columnas legadas fuera + CHECK de fases ---------------------------------
DO $$
DECLARE
    rotas text;
BEGIN
    SELECT string_agg(format('%s v%s (%s)', code, version, tipo_instrumento), ', ')
      INTO rotas
      FROM trd_series
     WHERE NOT (
            (tipo_instrumento = 'TRD' AND archivo_gestion_years IS NOT NULL AND archivo_central_years IS NOT NULL)
         OR (tipo_instrumento = 'TVD' AND archivo_central_years IS NOT NULL));
    IF rotas IS NOT NULL THEN
        RAISE EXCEPTION 'trd_series: filas sin las fases de retencion declaradas: % — repare (o recree) esos instrumentos antes de migrar (C-4/C6)', rotas;
    END IF;

    ALTER TABLE trd_series DROP COLUMN IF EXISTS retention_years;
    ALTER TABLE trd_series DROP COLUMN IF EXISTS total_retention;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema() AND t.relname = 'trd_series'
          AND c.conname = 'trd_series_fases_check') THEN
        ALTER TABLE trd_series ADD CONSTRAINT trd_series_fases_check CHECK (
               (tipo_instrumento = 'TRD' AND archivo_gestion_years IS NOT NULL AND archivo_central_years IS NOT NULL)
            OR (tipo_instrumento = 'TVD' AND archivo_central_years IS NOT NULL));
    END IF;

    -- Una TRD con 0 + 0 años no es «sin retención»: es un calendario que vence el día del
    -- cierre y, aguas abajo, un candado WORM que el cableado pospone para siempre. Una
    -- serie declara al menos un año entre las dos fases (la TVD, que no tiene gestión, no
    -- entra: su fase central ya es obligatoria).
    IF NOT EXISTS (SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema() AND t.relname = 'trd_series'
          AND c.conname = 'trd_series_retencion_minima_check') THEN
        ALTER TABLE trd_series ADD CONSTRAINT trd_series_retencion_minima_check CHECK (
            tipo_instrumento <> 'TRD' OR archivo_gestion_years + archivo_central_years >= 1);
    END IF;
END $$;

-- 5. jerarquía: mismo instrumento y TOPE DE DOS NIVELES -----------------------------
-- ADR-015 decía «serie > subserie» y nada lo imponía: una subserie podía tener
-- hijos y el EAD «admitía» series > subseries > subseries. Con el cuadro de
-- clasificación derivado del organigrama (C8) la serie queda en el segundo nivel
-- del CCD y la subserie en el tercero: más profundidad no tiene lugar en él.
CREATE OR REPLACE FUNCTION assert_trd_parent_mismo_tipo() RETURNS trigger AS $$
DECLARE
    parent_tipo   VARCHAR(3);
    parent_parent UUID;
    cambia_padre  BOOLEAN;
BEGIN
    IF NEW.parent_id IS NOT NULL THEN
        SELECT tipo_instrumento, parent_id INTO parent_tipo, parent_parent
          FROM trd_series WHERE id = NEW.parent_id;
        IF parent_tipo IS DISTINCT FROM NEW.tipo_instrumento THEN
            RAISE EXCEPTION 'trd_series: el padre % es de tipo % y la fila es de tipo % — la jerarquia no cruza instrumentos (ADR-026 D2)',
                NEW.parent_id, COALESCE(parent_tipo,'(inexistente)'), NEW.tipo_instrumento;
        END IF;
        IF TG_OP = 'UPDATE' THEN
            cambia_padre := NEW.parent_id IS DISTINCT FROM OLD.parent_id;
        ELSE
            cambia_padre := TRUE;
        END IF;
        IF cambia_padre THEN
            -- Serializa los cambios de forma de la jerarquía del schema: sin el candado, «alta de
            -- una subserie bajo S» y «S pasa a ser subserie» concurrentes comprueban cada una
            -- contra el estado anterior de la otra (aún sin confirmar) y confirman tres niveles.
            PERFORM pg_advisory_xact_lock(hashtext(current_schema() || ':trd_jerarquia'));
            SELECT parent_id INTO parent_parent FROM trd_series WHERE id = NEW.parent_id;
            IF parent_parent IS NOT NULL THEN
                RAISE EXCEPTION 'trd_jerarquia_dos_niveles: el padre % es a su vez una subserie — la jerarquia admite serie > subserie y nada mas (ADR-015)',
                    NEW.parent_id
                    USING ERRCODE = 'check_violation';
            END IF;
            IF EXISTS (SELECT 1 FROM trd_series h WHERE h.parent_id = NEW.id) THEN
                RAISE EXCEPTION 'trd_jerarquia_dos_niveles: la fila % ya tiene subseries y no puede pasar a ser subserie (ADR-015)',
                    NEW.id
                    USING ERRCODE = 'check_violation';
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
-- El trigger `trg_trd_series_parent_tipo` (039) ya apunta a esta funcion.

-- 6. vistas ---------------------------------------------------------------------
CREATE VIEW v_trd_series AS
    SELECT * FROM trd_series WHERE tipo_instrumento = 'TRD'
    WITH CHECK OPTION;

CREATE VIEW v_tvd_agrupaciones AS
    SELECT * FROM trd_series WHERE tipo_instrumento = 'TVD'
    WITH CHECK OPTION;

-- Rollback (referencia manual, no ejecutado por el runner): reponer las dos
-- columnas (NOT NULL DEFAULT 5 / 10), el cuerpo de la 039 de
-- `reject_trd_version_mutation` y `assert_trd_parent_mismo_tipo`, y las vistas.
