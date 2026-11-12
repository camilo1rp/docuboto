-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 076 (C-5, ADR-043; auditoría de cierre del lote C). search_path = tenant_{slug}.
--
-- 1) SELECCIÓN (S) con acto por expediente. Una valoración con disposición `S` ya no levanta el
--    hold `valoracion_pendiente` de todo el fondo: cada expediente queda en `seleccion_pendiente`
--    (valorada, sin aplicar, sin selección) hasta que el Comité decida POR EXPEDIENTE si entra a
--    la muestra (`conservar`: sigue el camino de CT, fija la retención y luego levanta) o se
--    descarta (`descartar`: se levanta el hold y queda elegible para el lote). El método de
--    muestreo se declara en la valoración (`tvd_valoracion.metodo_muestreo`, obligatorio con S).
--    `aplicada_at` de una valoración S solo puede fijarse con selección: lo exige el trigger.
-- 2) BAJA DE SOPORTE: `anexo_sha256` NOT NULL (sin huella no hay qué avalar destruir).
-- 3) `eliminacion_lotes` no se borra en `avalado|ejecutado`: el CASCADE arrastraría la baja de
--    soporte y el inventario, que son la evidencia de lo destruido.
--
-- Idempotente. Rollback: DROP TRIGGER trg_eliminacion_lotes_no_borrar_avalado; restaurar
-- `guard_expediente_valoracion_hold` de la 073; DROP de las columnas/constraints nuevas.

-- 1) ------------------------------------------------------------------------------------------
ALTER TABLE tvd_valoracion ADD COLUMN IF NOT EXISTS metodo_muestreo TEXT;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'ck_tvd_valoracion_muestreo'
                      AND conrelid = format('%I.tvd_valoracion', current_schema())::regclass) THEN
        -- NOT VALID: las valoraciones S anteriores no declararon método (no se inventa uno).
        ALTER TABLE tvd_valoracion ADD CONSTRAINT ck_tvd_valoracion_muestreo
            CHECK (disposicion <> 'S' OR length(btrim(coalesce(metodo_muestreo, ''))) >= 10) NOT VALID;
    END IF;
END $$;

ALTER TABLE expediente_valoracion_hold
    ADD COLUMN IF NOT EXISTS seleccion             VARCHAR(10),
    ADD COLUMN IF NOT EXISTS seleccion_por         UUID,
    ADD COLUMN IF NOT EXISTS seleccion_at          TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS seleccion_fundamento  TEXT;

ALTER TABLE expediente_valoracion_hold DROP CONSTRAINT IF EXISTS ck_valoracion_hold_seleccion;
ALTER TABLE expediente_valoracion_hold ADD CONSTRAINT ck_valoracion_hold_seleccion
    CHECK (seleccion IS NULL
           OR (seleccion IN ('conservar', 'descartar')
               AND seleccion_por IS NOT NULL AND seleccion_at IS NOT NULL
               AND length(btrim(coalesce(seleccion_fundamento, ''))) >= 10
               AND valoracion_id IS NOT NULL));

CREATE OR REPLACE FUNCTION guard_expediente_valoracion_hold() RETURNS trigger AS $$
DECLARE
    disp VARCHAR(2);
BEGIN
    IF NEW.hold_id IS DISTINCT FROM OLD.hold_id
       OR NEW.trd_serie_id IS DISTINCT FROM OLD.trd_serie_id
       OR NEW.expediente_id IS DISTINCT FROM OLD.expediente_id THEN
        RAISE EXCEPTION 'expediente_valoracion_hold: hold_id, trd_serie_id y expediente_id son inmutables';
    END IF;
    IF OLD.valoracion_id IS NOT NULL AND NEW.valoracion_id IS DISTINCT FROM OLD.valoracion_id THEN
        RAISE EXCEPTION 'expediente_valoracion_hold: valoracion_id ya registrada es inmutable';
    END IF;
    IF OLD.aplicada_at IS NOT NULL AND (NEW.aplicada_at IS DISTINCT FROM OLD.aplicada_at
                                        OR NEW.resultado IS DISTINCT FROM OLD.resultado) THEN
        RAISE EXCEPTION 'expediente_valoracion_hold: una valoración aplicada es inmutable';
    END IF;
    -- La selección se decide UNA vez (fill-once) y no se reescribe.
    IF OLD.seleccion IS NOT NULL AND (NEW.seleccion IS DISTINCT FROM OLD.seleccion
                                      OR NEW.seleccion_por IS DISTINCT FROM OLD.seleccion_por
                                      OR NEW.seleccion_at IS DISTINCT FROM OLD.seleccion_at
                                      OR NEW.seleccion_fundamento IS DISTINCT FROM OLD.seleccion_fundamento) THEN
        RAISE EXCEPTION 'expediente_valoracion_hold: la selección ya registrada es inmutable';
    END IF;
    -- Una valoración de SELECCIÓN no se aplica sin el acto de selección del expediente.
    IF NEW.aplicada_at IS NOT NULL AND OLD.aplicada_at IS NULL AND NEW.seleccion IS NULL THEN
        SELECT disposicion INTO disp FROM tvd_valoracion WHERE id = NEW.valoracion_id;
        IF disp = 'S' THEN
            RAISE EXCEPTION 'expediente_valoracion_hold: una valoración S exige la selección del expediente';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 2) ------------------------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM eliminacion_lote_soporte_citado WHERE anexo_sha256 IS NULL) THEN
        ALTER TABLE eliminacion_lote_soporte_citado ALTER COLUMN anexo_sha256 SET NOT NULL;
    ELSIF NOT EXISTS (SELECT 1 FROM pg_constraint
                       WHERE conname = 'ck_lote_soporte_sha256'
                         AND conrelid = format('%I.eliminacion_lote_soporte_citado', current_schema())::regclass) THEN
        -- Filas anteriores sin huella (append-only: no se corrigen): la regla rige para las nuevas.
        ALTER TABLE eliminacion_lote_soporte_citado
            ADD CONSTRAINT ck_lote_soporte_sha256 CHECK (anexo_sha256 IS NOT NULL) NOT VALID;
    END IF;
END $$;

-- 3) ------------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION reject_eliminacion_lote_delete_avalado() RETURNS trigger AS $$
BEGIN
    IF OLD.estado IN ('avalado', 'ejecutado') THEN
        RAISE EXCEPTION 'un lote de eliminación % no se borra: su inventario y su baja de soporte son la evidencia de lo destruido',
            OLD.estado;
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_eliminacion_lotes_no_borrar_avalado ON eliminacion_lotes;
CREATE TRIGGER trg_eliminacion_lotes_no_borrar_avalado
    BEFORE DELETE ON eliminacion_lotes
    FOR EACH ROW EXECUTE FUNCTION reject_eliminacion_lote_delete_avalado();
