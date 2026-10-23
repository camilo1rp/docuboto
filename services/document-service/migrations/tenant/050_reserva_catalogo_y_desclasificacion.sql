-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


-- Tenant migration 050 (C-2, ADR-039): catálogo cerrado de causales de reserva (C12) y
-- desclasificación desde nivel 3 con solicitud y aprobación por OTRA persona (C4).
--
-- 1) Las filas ya clasificadas cuya causal no cumple el catálogo pasan a `por_clasificar`
--    (valor explícito: no se inventa una causal; el índice público las muestra como
--    «pendiente de motivar» hasta que se re-motiven). No hay causal que deducir de un
--    texto libre, así que no hay backfill de datos.
-- 2) CHECK `radicados_causal_catalogo` (NOT VALID + VALIDATE, patrón 041): misma regla que
--    `app/core/causales_reserva.validar_clasificacion`; el guardián de tests compara los
--    códigos de este fichero con la constante de Python y con el `enum` de la tool MCP.
-- 3) Tabla `reclasificacion_solicitudes`: bajar de nivel 3 exige solicitud + aprobación de
--    una segunda persona con clearance suficiente. Aprobar EJECUTA, en la misma tx.
-- 4) Trigger de frontera en `radicados`: ninguna ruta (import, borrador, SQL, una ruta
--    futura) baja el nivel desde 3 sin una solicitud aprobada EN ESTA TRANSACCIÓN.
--
-- Idempotente. Rollback (en este orden):
--   DROP TRIGGER IF EXISTS trg_radicados_desclasificacion ON radicados;
--   DROP FUNCTION IF EXISTS radicados_desclasificacion_guard();
--   DROP TABLE IF EXISTS reclasificacion_solicitudes;
--   DROP FUNCTION IF EXISTS reclasificacion_solicitud_guard();
--   ALTER TABLE radicados DROP CONSTRAINT IF EXISTS radicados_causal_catalogo;
--   (las filas `por_clasificar` no se restauran: el texto libre original no se guardó.)

-- 1) lo clasificado que no cumple el catálogo --------------------------------------------
UPDATE radicados
   SET clasificacion_causal = 'por_clasificar'
 WHERE nivel_seguridad >= 2
   AND (   clasificacion_causal = 'por_clasificar'
        OR (nivel_seguridad = 3
            AND clasificacion_causal IN ('art18_a_intimidad', 'art18_b_vida_salud_seguridad', 'art18_c_secretos')
            AND clasificacion_plazo_meses IS NULL)
        OR (nivel_seguridad = 2
            AND clasificacion_causal IN ('art19_a_defensa_seguridad_nacional', 'art19_b_seguridad_publica',
                'art19_c_relaciones_internacionales', 'art19_d_prevencion_investigacion_delitos',
                'art19_e_debido_proceso', 'art19_f_administracion_justicia',
                'art19_g_infancia_adolescencia', 'art19_h_estabilidad_macroeconomica',
                'art19_i_salud_publica', 'art19_par_deliberativo')
            AND clasificacion_plazo_meses BETWEEN 1 AND 180)
       ) IS NOT TRUE;

UPDATE radicados
   SET clasificacion_causal = NULL, clasificacion_plazo_meses = NULL
 WHERE nivel_seguridad = 1
   AND (clasificacion_causal IS NOT NULL OR clasificacion_plazo_meses IS NOT NULL);

-- 2) CHECK del catálogo --------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'radicados_causal_catalogo'
           AND conrelid = format('%I.radicados', current_schema())::regclass
    ) THEN
        ALTER TABLE radicados
            ADD CONSTRAINT radicados_causal_catalogo
            -- COALESCE: un CHECK con NULL (causal o plazo ausentes) PASA; aquí NULL es rechazo.
            CHECK (COALESCE(
                (nivel_seguridad = 1 AND clasificacion_causal IS NULL
                                     AND clasificacion_plazo_meses IS NULL)
                OR (nivel_seguridad >= 2 AND clasificacion_causal = 'por_clasificar')
                OR (nivel_seguridad = 3
                    AND clasificacion_causal IN ('art18_a_intimidad', 'art18_b_vida_salud_seguridad', 'art18_c_secretos')
                    AND clasificacion_plazo_meses IS NULL)
                OR (nivel_seguridad = 2
                    AND clasificacion_causal IN ('art19_a_defensa_seguridad_nacional', 'art19_b_seguridad_publica',
                        'art19_c_relaciones_internacionales', 'art19_d_prevencion_investigacion_delitos',
                        'art19_e_debido_proceso', 'art19_f_administracion_justicia',
                        'art19_g_infancia_adolescencia', 'art19_h_estabilidad_macroeconomica',
                        'art19_i_salud_publica', 'art19_par_deliberativo')
                    AND clasificacion_plazo_meses BETWEEN 1 AND 180),
                FALSE)
            ) NOT VALID;
    END IF;
    ALTER TABLE radicados VALIDATE CONSTRAINT radicados_causal_catalogo;
END $$;

-- 3) solicitudes de desclasificación ------------------------------------------------------
CREATE TABLE IF NOT EXISTS reclasificacion_solicitudes (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- CASCADE: la solicitud es parte del expediente del radicado; el único DELETE de
    -- radicados es la purga por disposición final (acta firmada) y el audit_log conserva
    -- la traza. El trigger de abajo prohíbe cualquier otro borrado.
    radicado_id         UUID NOT NULL REFERENCES radicados(id) ON DELETE CASCADE,
    -- Instantánea del nivel al pedir. = NIVEL_DOBLE_FIRMA (core/causales_reserva.py).
    nivel_anterior      SMALLINT NOT NULL CHECK (nivel_anterior = 3),
    nivel_nuevo         SMALLINT NOT NULL CHECK (nivel_nuevo IN (1, 2) AND nivel_nuevo < nivel_anterior),
    motivo              TEXT NOT NULL CHECK (length(btrim(motivo)) >= 10),
    causal_codigo       TEXT,
    fundamento_juridico TEXT,
    plazo_meses         INTEGER,
    estado              TEXT NOT NULL DEFAULT 'pendiente'
                        CHECK (estado IN ('pendiente', 'aprobada', 'rechazada', 'retirada', 'caducada')),
    solicitado_por      UUID NOT NULL,
    solicitado_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    vence_at            TIMESTAMPTZ NOT NULL DEFAULT now() + interval '15 days',
    resuelto_por        UUID,
    resuelto_at         TIMESTAMPTZ,
    resolucion_motivo   TEXT,
    -- La destinación lleva la misma regla que el CHECK de radicados.
    CONSTRAINT rs_causal_destino CHECK (COALESCE(
           (nivel_nuevo = 1 AND causal_codigo IS NULL AND plazo_meses IS NULL)
        OR (nivel_nuevo = 2
            AND causal_codigo IN ('art19_a_defensa_seguridad_nacional', 'art19_b_seguridad_publica',
                'art19_c_relaciones_internacionales', 'art19_d_prevencion_investigacion_delitos',
                'art19_e_debido_proceso', 'art19_f_administracion_justicia',
                'art19_g_infancia_adolescencia', 'art19_h_estabilidad_macroeconomica',
                'art19_i_salud_publica', 'art19_par_deliberativo')
            AND fundamento_juridico IS NOT NULL AND btrim(fundamento_juridico) <> ''
            AND plazo_meses BETWEEN 1 AND 180),
        FALSE)
    ),
    CONSTRAINT rs_resuelta_forma CHECK (
        estado IN ('pendiente', 'caducada')
        OR (resuelto_por IS NOT NULL AND resuelto_at IS NOT NULL)
    ),
    -- Separación de funciones en la base: quien pide no decide (ni con un UPDATE directo).
    CONSTRAINT rs_separacion_funciones CHECK (
        estado NOT IN ('aprobada', 'rechazada') OR resuelto_por <> solicitado_por
    ),
    CONSTRAINT rs_retirada_por_solicitante CHECK (
        estado <> 'retirada' OR resuelto_por = solicitado_por
    ),
    CONSTRAINT rs_rechazo_motivado CHECK (
        estado <> 'rechazada' OR length(btrim(coalesce(resolucion_motivo, ''))) >= 10
    )
);

-- A lo sumo UNA solicitud viva por radicado.
CREATE UNIQUE INDEX IF NOT EXISTS ux_rs_pendiente
    ON reclasificacion_solicitudes (radicado_id) WHERE estado = 'pendiente';
CREATE INDEX IF NOT EXISTS ix_rs_cola
    ON reclasificacion_solicitudes (solicitado_at) WHERE estado = 'pendiente';
CREATE INDEX IF NOT EXISTS ix_rs_radicado
    ON reclasificacion_solicitudes (radicado_id, solicitado_at DESC);

-- Una solicitud se resuelve UNA vez y su pedido no se reescribe; no se borra (salvo en
-- cascada desde la purga del radicado: ahí el trigger corre anidado, depth >= 2).
CREATE OR REPLACE FUNCTION reclasificacion_solicitud_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF pg_trigger_depth() < 2 THEN
            RAISE EXCEPTION 'reclasificacion_solicitudes no se borra (traza de la desclasificación)'
                USING ERRCODE = 'P0001';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.estado <> 'pendiente' THEN
        RAISE EXCEPTION 'la solicitud de desclasificación ya está resuelta (%)', OLD.estado
            USING ERRCODE = 'P0001';
    END IF;
    IF NEW.estado = 'pendiente' THEN
        RAISE EXCEPTION 'una solicitud pendiente solo cambia a un estado final'
            USING ERRCODE = 'P0001';
    END IF;
    IF (NEW.id, NEW.radicado_id, NEW.nivel_anterior, NEW.nivel_nuevo, NEW.motivo,
        NEW.causal_codigo, NEW.fundamento_juridico, NEW.plazo_meses,
        NEW.solicitado_por, NEW.solicitado_at, NEW.vence_at)
       IS DISTINCT FROM
       (OLD.id, OLD.radicado_id, OLD.nivel_anterior, OLD.nivel_nuevo, OLD.motivo,
        OLD.causal_codigo, OLD.fundamento_juridico, OLD.plazo_meses,
        OLD.solicitado_por, OLD.solicitado_at, OLD.vence_at) THEN
        RAISE EXCEPTION 'el pedido de una solicitud de desclasificación no se reescribe'
            USING ERRCODE = 'P0001';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_reclasificacion_solicitud_guard ON reclasificacion_solicitudes;
CREATE TRIGGER trg_reclasificacion_solicitud_guard
    BEFORE UPDATE OR DELETE ON reclasificacion_solicitudes
    FOR EACH ROW EXECUTE FUNCTION reclasificacion_solicitud_guard();

-- 4) frontera: bajar desde nivel 3 solo con una solicitud aprobada en ESTA transacción ----
-- `resuelto_at = now()` (now() = inicio de la tx) ata la aprobación a este acto: una
-- aprobación vieja no sirve de pase para una desclasificación posterior.
CREATE OR REPLACE FUNCTION radicados_desclasificacion_guard() RETURNS trigger AS $$
BEGIN
    IF OLD.nivel_seguridad >= 3 AND NEW.nivel_seguridad < OLD.nivel_seguridad THEN
        IF NOT EXISTS (
            SELECT 1 FROM reclasificacion_solicitudes s
             WHERE s.radicado_id = NEW.id
               AND s.estado = 'aprobada'
               AND s.nivel_anterior = OLD.nivel_seguridad
               AND s.nivel_nuevo = NEW.nivel_seguridad
               AND s.resuelto_at = now()
        ) THEN
            RAISE EXCEPTION
                'bajar el nivel de un radicado clasificado exige una solicitud de desclasificación aprobada por otra persona'
                USING ERRCODE = 'P0001', HINT = 'desclasificacion_requiere_aprobacion';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_radicados_desclasificacion ON radicados;
CREATE TRIGGER trg_radicados_desclasificacion
    BEFORE UPDATE OF nivel_seguridad ON radicados
    FOR EACH ROW EXECUTE FUNCTION radicados_desclasificacion_guard();
