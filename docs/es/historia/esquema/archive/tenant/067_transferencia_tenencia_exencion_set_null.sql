-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 067 (B4 re-auditoría; Ley 594/2000 arts. 15/26): la exención de la 065
-- «una actualización que solo ANULA o conserva valores no se valida» cubría también un UPDATE
-- manual (`UPDATE transferencias SET ubicacion_destino_id = NULL`), que borraba la tenencia
-- física afirmada sin dejar el 23514. Se restringe a la acción referencial `ON DELETE SET NULL`
-- (`pg_trigger_depth() > 1`); el UPDATE sin cambios en las tres columnas sigue exento.
--
-- Idempotente (CREATE OR REPLACE). El trigger de la 065 no cambia. search_path = tenant_{slug}.

CREATE OR REPLACE FUNCTION transferencia_guard_tenencia() RETURNS trigger AS $$
DECLARE
    v_tenencia  UUID[];
    v_tipo_o    TEXT;
    v_tipo_d    TEXT;
    v_esp_o     TEXT;
    v_esp_d     TEXT;
    v_codigo    TEXT;
BEGIN
    -- Sin cambio alguno en las tres columnas: no es una afirmación nueva sobre la tenencia.
    IF TG_OP = 'UPDATE'
       AND NEW.expediente_id IS NOT DISTINCT FROM OLD.expediente_id
       AND NEW.ubicacion_origen_id IS NOT DISTINCT FROM OLD.ubicacion_origen_id
       AND NEW.ubicacion_destino_id IS NOT DISTINCT FROM OLD.ubicacion_destino_id
    THEN
        RETURN NEW;
    END IF;
    -- La exención «UPDATE que solo anula» es EXCLUSIVA de la acción referencial
    -- `ON DELETE SET NULL` (corre dentro de un trigger de RI: pg_trigger_depth() > 1). Un UPDATE
    -- manual que anule el par se valida como cualquier otro (B4, re-auditoría).
    IF TG_OP = 'UPDATE'
       AND pg_trigger_depth() > 1
       AND NEW.expediente_id IS NOT DISTINCT FROM OLD.expediente_id
       AND (NEW.ubicacion_origen_id IS NULL OR NEW.ubicacion_origen_id IS NOT DISTINCT FROM OLD.ubicacion_origen_id)
       AND (NEW.ubicacion_destino_id IS NULL OR NEW.ubicacion_destino_id IS NOT DISTINCT FROM OLD.ubicacion_destino_id)
    THEN
        RETURN NEW;
    END IF;

    SELECT COALESCE(array_agg(s.ubicacion_id), ARRAY[]::UUID[]) INTO v_tenencia
      FROM (SELECT u.ubicacion_id
              FROM expediente_unidad eu
              JOIN unidad_conservacion u ON u.id = eu.unidad_id
             WHERE eu.expediente_id = NEW.expediente_id
               AND u.ubicacion_id IS NOT NULL
               FOR SHARE OF eu) s;

    v_codigo := NULL;
    IF cardinality(v_tenencia) > 0 THEN
        IF NEW.ubicacion_origen_id IS NULL OR NEW.ubicacion_destino_id IS NULL THEN
            v_codigo := 'ubicaciones_requeridas_fisico';
        END IF;
    ELSIF (NEW.ubicacion_origen_id IS NULL) <> (NEW.ubicacion_destino_id IS NULL) THEN
        v_codigo := 'ubicaciones_par_incompleto';
    ELSIF NEW.ubicacion_origen_id IS NOT NULL THEN
        v_codigo := 'ubicacion_sin_tenencia';
    END IF;

    IF v_codigo IS NULL AND NEW.ubicacion_origen_id IS NOT NULL AND NEW.ubicacion_destino_id IS NOT NULL THEN
        IF NEW.ubicacion_origen_id = NEW.ubicacion_destino_id THEN
            v_codigo := 'ubicaciones_origen_destino_iguales';
        ELSIF NOT (NEW.ubicacion_origen_id = ANY (v_tenencia)) THEN
            v_codigo := 'ubicacion_origen_no_actual';
        ELSE
            SELECT tipo_archivo INTO v_tipo_o FROM ubicacion WHERE id = NEW.ubicacion_origen_id;
            SELECT tipo_archivo INTO v_tipo_d FROM ubicacion WHERE id = NEW.ubicacion_destino_id;
            v_esp_o := CASE NEW.tipo WHEN 'primaria' THEN 'gestion' ELSE 'central' END;
            v_esp_d := CASE NEW.tipo WHEN 'primaria' THEN 'central' ELSE 'historico' END;
            IF v_tipo_o IS DISTINCT FROM v_esp_o OR v_tipo_d IS DISTINCT FROM v_esp_d THEN
                v_codigo := 'ubicacion_incoherente_con_tipo';
            END IF;
        END IF;
    END IF;

    IF v_codigo IS NOT NULL THEN
        RAISE EXCEPTION '%', v_codigo
            USING ERRCODE = '23514', CONSTRAINT = 'ck_transferencias_tenencia_fisica';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Rollback manual: re-ejecutar la definición de la función de la 065.
