-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 065 (B4, Ley 594/2000 arts. 15/26; Anexo FUID Ac. AGN 001/2024):
-- la TENENCIA FÍSICA de una transferencia se garantiza en la base, no solo en el servicio.
--
-- `TransferenciaService._validar_ubicaciones` ya cotejaba el par origen/destino contra las
-- ubicaciones actuales del expediente, pero solo en esa ruta: un INSERT directo (otro
-- servicio, un script de operación) podía fijar un par sin tenencia que luego el acta FUID
-- atestiguaría como movimiento real. Este trigger replica las mismas reglas, con el MISMO
-- código que el 422 del servicio en `MESSAGE` y `ERRCODE = '23514'` (check_violation), y
-- bloquea los vínculos `expediente_unidad` con `FOR SHARE` hasta el commit (como el servicio).
--
-- Reglas (T = ubicaciones de las unidades que contienen al expediente):
--   * T no vacío  => el par es obligatorio          (ubicaciones_requeridas_fisico)
--   * T vacío     => ambos o ninguno                (ubicaciones_par_incompleto)
--   * T vacío     => sin par                        (ubicacion_sin_tenencia)
--   * origen <> destino                             (ubicaciones_origen_destino_iguales)
--   * origen pertenece a T                          (ubicacion_origen_no_actual)
--   * tipo_archivo coherente con el tipo            (ubicacion_incoherente_con_tipo)
--
-- Solo filas nuevas o columnas relevantes tocadas: el trigger es `UPDATE OF` esas tres
-- columnas, así que `enviar`/`recibir`/`rechazar` no lo disparan y las transferencias
-- heredadas no se revalidan. Las acciones referenciales `ON DELETE SET NULL` (borrar una
-- ubicación) SÍ tocan esas columnas: una actualización que solo ANULA o conserva valores
-- no se valida (no es una nueva afirmación sobre la tenencia).
--
-- Declarado fuera de alcance: bloquear la desvinculación de unidades con transferencia
-- abierta.
--
-- Idempotente (CREATE OR REPLACE + DROP TRIGGER IF EXISTS). search_path = tenant_{slug}.

CREATE OR REPLACE FUNCTION transferencia_guard_tenencia() RETURNS trigger AS $$
DECLARE
    v_tenencia  UUID[];
    v_tipo_o    TEXT;
    v_tipo_d    TEXT;
    v_esp_o     TEXT;
    v_esp_d     TEXT;
    v_codigo    TEXT;
BEGIN
    IF TG_OP = 'UPDATE'
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

DROP TRIGGER IF EXISTS trg_transferencia_guard_tenencia ON transferencias;
CREATE TRIGGER trg_transferencia_guard_tenencia
    BEFORE INSERT OR UPDATE OF expediente_id, ubicacion_origen_id, ubicacion_destino_id
    ON transferencias
    FOR EACH ROW EXECUTE FUNCTION transferencia_guard_tenencia();

-- Rollback manual (no automatizado):
-- DROP TRIGGER IF EXISTS trg_transferencia_guard_tenencia ON transferencias;
-- DROP FUNCTION IF EXISTS transferencia_guard_tenencia();
