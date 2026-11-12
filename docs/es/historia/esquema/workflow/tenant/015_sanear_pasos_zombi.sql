-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (ADR-028, remediación de auditoría): sanea los pasos «zombi».
--
-- Un paso zombi es un paso ACTIVO (pending/in_progress) de un radicado TERMINAL
-- (closed/anulado). Nace de dos caminos anteriores al ADR: la anulación no
-- cancelaba el paso, y el `PATCH status` manual cerraba el radicado sin cerrar el
-- paso. Un zombi aparece en bandeja, cuenta como vencido, entra en la cascada y
-- NO se puede cerrar (el radicado ya es terminal: todo acto responde 409).
--
-- Tratamiento, por radicado terminal con paso activo:
--   anulado -> el paso pasa a 'cancelled'  (el trámite se canceló con el radicado)
--   closed  -> el paso pasa a 'completed'  (el radicado se cerró: el paso concluyó)
-- y cada paso tratado deja (a) un evento operativo `saneamiento_terminal` en
-- `flow_events` (append-only) y (b) una fila de evidencia en
-- `flow_step_saneamiento` (solo inserción, con el estado antes/después del paso).
-- No toca `radicados`. El asiento-ancla en `audit_log` (conteo + SHA-256 del
-- contenido ordenado) lo escribe el runner (`anclar_saneamiento_015`), nunca SQL.
--
-- Idempotente: tras la 1.ª corrida no quedan zombis, la 2.ª no encuentra ninguno.
-- Si `radicados` no existe en este schema, no hace nada.
--
-- Rollback (la evidencia permite restaurar; los eventos append-only se conservan):
--   UPDATE flow_steps f SET status = e.paso_antes, completed_at = NULL
--     FROM flow_step_saneamiento e WHERE e.step_id = f.id AND f.status = e.paso_despues;
--   DROP TABLE flow_step_saneamiento;

CREATE TABLE IF NOT EXISTS flow_step_saneamiento (
    id               BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    step_id          UUID        NOT NULL,
    radicado_id      UUID        NOT NULL,
    tracking_number  VARCHAR(50) NOT NULL,
    estado_radicado  VARCHAR(20) NOT NULL,  -- closed | anulado
    paso_antes       VARCHAR(20) NOT NULL,
    paso_despues     VARCHAR(20) NOT NULL,
    flow_event_id    UUID        NOT NULL,
    origen           VARCHAR(40) NOT NULL DEFAULT 'migracion_015',
    at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION reject_flow_step_saneamiento_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'flow_step_saneamiento is append-only (evidencia de la migración 015)';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_flow_step_saneamiento_immutable ON flow_step_saneamiento;
CREATE TRIGGER trg_flow_step_saneamiento_immutable
    BEFORE UPDATE OR DELETE ON flow_step_saneamiento
    FOR EACH ROW EXECUTE FUNCTION reject_flow_step_saneamiento_mutation();

DO $$
DECLARE
    v_saneados INTEGER;
BEGIN
    IF to_regclass(format('%I.radicados', current_schema())) IS NULL THEN
        RAISE NOTICE 'radicados no existe en %: saneamiento de pasos omitido', current_schema();
        RETURN;
    END IF;

    WITH zombis AS (
        SELECT f.id AS step_id, f.radicado_id, f.tracking_number, f.to_dept,
               f.status AS paso_antes, r.status AS estado_radicado,
               gen_random_uuid() AS ev_id
          FROM flow_steps f
          JOIN radicados r ON r.id = f.radicado_id
         WHERE f.status IN ('pending', 'in_progress')
           AND r.status IN ('closed', 'anulado')
    ),
    upd AS (
        UPDATE flow_steps f
           SET status = CASE WHEN z.estado_radicado = 'anulado'
                             THEN 'cancelled' ELSE 'completed' END,
               completed_at = now()
          FROM zombis z
         WHERE f.id = z.step_id
        RETURNING z.step_id, z.radicado_id, z.tracking_number, z.to_dept,
                  z.paso_antes, f.status AS paso_despues, z.estado_radicado, z.ev_id
    ),
    ev AS (
        INSERT INTO flow_events
               (id, radicado_id, tracking_number, tipo_tx, from_dept, to_dept,
                actor, comentario, detalles)
        SELECT ev_id, radicado_id, tracking_number, 'saneamiento_terminal', to_dept, to_dept,
               NULL,
               'Paso activo de un radicado ' || estado_radicado || ' saneado por la migración 015',
               jsonb_build_object('step_id', step_id, 'paso_antes', paso_antes,
                                  'paso_despues', paso_despues,
                                  'estado_radicado', estado_radicado)
          FROM upd
        RETURNING id
    )
    INSERT INTO flow_step_saneamiento
           (step_id, radicado_id, tracking_number, estado_radicado,
            paso_antes, paso_despues, flow_event_id)
    SELECT step_id, radicado_id, tracking_number, estado_radicado,
           paso_antes, paso_despues, ev_id
      FROM upd;

    GET DIAGNOSTICS v_saneados = ROW_COUNT;
    RAISE NOTICE 'saneamiento de pasos zombi (%): % pasos', current_schema(), v_saneados;
END $$;
