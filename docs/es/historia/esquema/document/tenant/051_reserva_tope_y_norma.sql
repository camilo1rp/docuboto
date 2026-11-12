-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 051 (C-2, ADR-039, auditoría de cierre del lote C):
--
-- 1) TOPE DE 15 AÑOS DESDE LA GENERACIÓN (Ley 1712/2014 art. 22; Decreto 103/2015
--    art. 29, hoy DUR 1081/2015). La reserva del art. 19 no puede durar más de 15 años
--    contados desde que se GENERÓ la información (`registered_at`: la fecha de
--    radicación o, en un paquete importado, la del documento), no desde que se
--    clasificó. El plazo se mide SIEMPRE contra la generación: reiniciar
--    `clasificado_at` (2 -> 3 -> 2) no alarga la reserva, porque el plazo permitido es
--    `180 - meses transcurridos hasta la clasificación`.
--    `reserva_meses_entre(a, b)` = meses completos entre dos instantes (UTC), la misma
--    cuenta que usa el servicio al validar (la lee de la base: una sola definición).
-- 2) `clasificacion_norma`: cita normativa PUBLICABLE y controlada por formato (Decreto
--    103/2015 art. 40: «fundamento constitucional o legal»), separada del
--    `clasificacion_fundamento` libre, que es interno y puede transportar contenido.
--
-- Lo que no cumple el tope pasa a `por_clasificar` (valor explícito, patrón de la 050).
-- Idempotente. Rollback (en este orden):
--   ALTER TABLE radicados DROP CONSTRAINT IF EXISTS radicados_reserva_tope_15_anios;
--   ALTER TABLE radicados DROP CONSTRAINT IF EXISTS radicados_norma_controlada;
--   ALTER TABLE radicados DROP COLUMN IF EXISTS clasificacion_norma;
--   ALTER TABLE reclasificacion_solicitudes DROP CONSTRAINT IF EXISTS rs_norma_controlada;
--   ALTER TABLE reclasificacion_solicitudes DROP COLUMN IF EXISTS fundamento_norma;
--   DROP FUNCTION IF EXISTS reserva_meses_entre(timestamptz, timestamptz);

CREATE OR REPLACE FUNCTION reserva_meses_entre(desde timestamptz, hasta timestamptz)
RETURNS integer AS $$
    SELECT GREATEST(0, (
        EXTRACT(YEAR  FROM age(hasta AT TIME ZONE 'UTC', desde AT TIME ZONE 'UTC'))::int * 12
      + EXTRACT(MONTH FROM age(hasta AT TIME ZONE 'UTC', desde AT TIME ZONE 'UTC'))::int
    ))
$$ LANGUAGE sql IMMUTABLE;

-- 1) tope -------------------------------------------------------------------------------
UPDATE radicados
   SET clasificacion_causal = 'por_clasificar'
 WHERE nivel_seguridad = 2
   AND clasificacion_causal <> 'por_clasificar'
   AND clasificacion_plazo_meses > 180 - reserva_meses_entre(
           registered_at, COALESCE(clasificado_at, registered_at));

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'radicados_reserva_tope_15_anios'
           AND conrelid = format('%I.radicados', current_schema())::regclass
    ) THEN
        ALTER TABLE radicados
            ADD CONSTRAINT radicados_reserva_tope_15_anios
            CHECK (COALESCE(
                nivel_seguridad <> 2
                OR clasificacion_causal = 'por_clasificar'
                OR clasificacion_plazo_meses IS NULL
                OR clasificacion_plazo_meses
                   <= 180 - reserva_meses_entre(registered_at, COALESCE(clasificado_at, registered_at)),
                FALSE)
            ) NOT VALID;
    END IF;
    ALTER TABLE radicados VALIDATE CONSTRAINT radicados_reserva_tope_15_anios;
END $$;

-- 2) cita normativa publicable ------------------------------------------------------------
ALTER TABLE radicados ADD COLUMN IF NOT EXISTS clasificacion_norma TEXT;
ALTER TABLE reclasificacion_solicitudes ADD COLUMN IF NOT EXISTS fundamento_norma TEXT;

-- Misma regla que `app/core/causales_reserva.CITA_NORMA_RE` (el guardián las compara):
-- una norma, no una frase. Tipo de norma + número/artículo, 160 caracteres como máximo.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'radicados_norma_controlada'
           AND conrelid = format('%I.radicados', current_schema())::regclass
    ) THEN
        ALTER TABLE radicados
            ADD CONSTRAINT radicados_norma_controlada
            CHECK (clasificacion_norma IS NULL OR (
                nivel_seguridad >= 2
                AND clasificacion_norma ~ '^(Constitución Política|Ley|Decreto-Ley|Decreto|Acuerdo|Resolución|Código)[ A-Za-z0-9ÁÉÍÓÚÑáéíóúñ.,°º/()-]{1,150}$'
                AND clasificacion_norma ~ '[0-9]'
            )) NOT VALID;
    END IF;
    ALTER TABLE radicados VALIDATE CONSTRAINT radicados_norma_controlada;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'rs_norma_controlada'
           AND conrelid = format('%I.reclasificacion_solicitudes', current_schema())::regclass
    ) THEN
        ALTER TABLE reclasificacion_solicitudes
            ADD CONSTRAINT rs_norma_controlada
            CHECK (fundamento_norma IS NULL OR (
                nivel_nuevo = 2
                AND fundamento_norma ~ '^(Constitución Política|Ley|Decreto-Ley|Decreto|Acuerdo|Resolución|Código)[ A-Za-z0-9ÁÉÍÓÚÑáéíóúñ.,°º/()-]{1,150}$'
                AND fundamento_norma ~ '[0-9]'
            )) NOT VALID;
    END IF;
    ALTER TABLE reclasificacion_solicitudes VALIDATE CONSTRAINT rs_norma_controlada;
END $$;

-- El pedido de una solicitud no se reescribe: ahora incluye la cita normativa.
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
        NEW.causal_codigo, NEW.fundamento_juridico, NEW.fundamento_norma, NEW.plazo_meses,
        NEW.solicitado_por, NEW.solicitado_at, NEW.vence_at)
       IS DISTINCT FROM
       (OLD.id, OLD.radicado_id, OLD.nivel_anterior, OLD.nivel_nuevo, OLD.motivo,
        OLD.causal_codigo, OLD.fundamento_juridico, OLD.fundamento_norma, OLD.plazo_meses,
        OLD.solicitado_por, OLD.solicitado_at, OLD.vence_at) THEN
        RAISE EXCEPTION 'el pedido de una solicitud de desclasificación no se reescribe'
            USING ERRCODE = 'P0001';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
