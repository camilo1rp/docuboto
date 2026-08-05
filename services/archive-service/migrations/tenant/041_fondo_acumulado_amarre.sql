-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (ADR-026, Increment 2): amarre de expedientes de
-- FONDO ACUMULADO a una TVD (ADR-026 D3) + relajación de la Capa 4
-- (`trg_expedientes_pin_tipo`, instalada ESTRICTA por la 039) a la regla
-- `origen <-> tipo_instrumento`.
--
-- Guards acotados a `current_schema()` (ADR-002/012, lección de las
-- migraciones 016/017/019/035, reparadas por la 036).

-- ---------------------------------------------------------------------------
-- 1. `expedientes.origen` + fechas extremas del fondo acumulado
-- ---------------------------------------------------------------------------
-- DEFAULT 'corriente' describe un HECHO VERIFICABLE (todo expediente
-- anterior a esta migración se abrió y cerró bajo el circuito TRD ordinario
-- -- el trigger ESTRICTO de la 039 lo garantiza: hasta hoy, NINGÚN
-- expediente pudo amarrarse a una TVD) -- mismo criterio que el
-- `tipo_instrumento` DEFAULT 'TRD' de la 039, no el de `estado` de la 037.
ALTER TABLE expedientes
    ADD COLUMN IF NOT EXISTS origen                 VARCHAR(20) NOT NULL DEFAULT 'corriente',
    ADD COLUMN IF NOT EXISTS fecha_extrema_inicial   DATE,
    ADD COLUMN IF NOT EXISTS fecha_extrema_final     DATE;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema() AND t.relname = 'expedientes'
          AND c.conname = 'expedientes_origen_check') THEN
        ALTER TABLE expedientes ADD CONSTRAINT expedientes_origen_check
            CHECK (origen IN ('corriente', 'fondo_acumulado'));
    END IF;

    -- ADR-026 D4: la fecha base del calendario de un fondo acumulado es la
    -- EXTREMA FINAL, nunca `closed_at` -- por eso, a diferencia de
    -- `trd_series_tvd_forma_check` (que exige TAMBIÉN `fondo_nombre` y
    -- `justificacion_valoracion`, propios del INSTRUMENTO, no del
    -- expediente), aquí solo `fecha_extrema_final` es NOT NULL bajo CHECK:
    -- es la única columna que este `compute_retention` va a leer. `>= inicial`
    -- solo se exige cuando ambas están presentes -- `fecha_extrema_inicial`
    -- es informativa (documenta el rango del fondo) y puede desconocerse.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema() AND t.relname = 'expedientes'
          AND c.conname = 'expedientes_fondo_acumulado_forma_check') THEN
        ALTER TABLE expedientes ADD CONSTRAINT expedientes_fondo_acumulado_forma_check
            CHECK (origen <> 'fondo_acumulado' OR (
                fecha_extrema_final IS NOT NULL AND (
                    fecha_extrema_inicial IS NULL
                    OR fecha_extrema_inicial <= fecha_extrema_final
                )));
    END IF;

    -- Reverso (negación declarada, mismo criterio que
    -- `trd_series_trd_forma_check` de la 039): un expediente `corriente` no
    -- puede cargar fechas extremas de un fondo que no es. Sin esto, un
    -- expediente `corriente` con `fecha_extrema_final` residual (p. ej. tras
    -- un `PATCH` mal formado, si algún día `origen` se hiciera editable)
    -- quedaría en una forma ambigua que ningún lector podría distinguir de
    -- un fondo acumulado sin marcar.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema() AND t.relname = 'expedientes'
          AND c.conname = 'expedientes_corriente_forma_check') THEN
        ALTER TABLE expedientes ADD CONSTRAINT expedientes_corriente_forma_check
            CHECK (origen <> 'corriente' OR (
                fecha_extrema_inicial IS NULL AND fecha_extrema_final IS NULL
            ));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_expedientes_origen ON expedientes (origen);

-- ---------------------------------------------------------------------------
-- 2. Capa 4 relajada: `origen <-> tipo_instrumento`, no ya el bloqueo total
--    de la 039. El trigger SIGUE existiendo -- ninguna combinación cruzada
--    (corriente/TVD, fondo_acumulado/TRD) puede colarse -- solo cambia la
--    regla que aplica. `CREATE OR REPLACE FUNCTION` basta: el trigger
--    `trg_expedientes_pin_tipo` (039) ya apunta a esta función.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION assert_expediente_pin_tipo_instrumento() RETURNS trigger AS $$
DECLARE
    pin_tipo VARCHAR(3);
BEGIN
    IF NEW.trd_serie_id IS NOT NULL
       AND (TG_OP = 'INSERT' OR NEW.trd_serie_id IS DISTINCT FROM OLD.trd_serie_id) THEN
        SELECT tipo_instrumento INTO pin_tipo FROM trd_series WHERE id = NEW.trd_serie_id;

        IF NEW.origen = 'corriente' AND pin_tipo = 'TVD' THEN
            RAISE EXCEPTION 'expedientes: un expediente origen=corriente no puede amarrarse a la TVD % -- el fondo acumulado se registra con origen=fondo_acumulado (ADR-026 D3)',
                NEW.trd_serie_id;
        ELSIF NEW.origen = 'fondo_acumulado' AND pin_tipo = 'TRD' THEN
            RAISE EXCEPTION 'expedientes: un expediente origen=fondo_acumulado no puede amarrarse a la TRD % -- el fondo acumulado se valora con una TVD (ADR-026 D3)',
                NEW.trd_serie_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Rollback (referencia manual, no ejecutado por el runner):
--   ALTER TABLE expedientes DROP CONSTRAINT IF EXISTS expedientes_corriente_forma_check;
--   ALTER TABLE expedientes DROP CONSTRAINT IF EXISTS expedientes_fondo_acumulado_forma_check;
--   ALTER TABLE expedientes DROP CONSTRAINT IF EXISTS expedientes_origen_check;
--   DROP INDEX IF EXISTS ix_expedientes_origen;
--   (el rollback de `assert_expediente_pin_tipo_instrumento` a su forma
--    ESTRICTA -- 039 -- NO puede ejecutarse si ya existe algún expediente
--    origen='fondo_acumulado')
