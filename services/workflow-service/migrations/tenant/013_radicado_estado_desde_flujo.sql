-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (ADR-028): el flujo mueve el estado del radicado.
--
-- Recalcula `radicados.status` de los radicados existentes desde `flow_steps`,
-- con el CASE gemelo LITERAL de la función pura
-- `app/services/estado_radicado.py::derivar_estado_radicado` (la prueba de
-- integración compara cada fila con la función Python):
--
--   terminal (closed|anulado)       -> no se toca
--   paso activo 'pending'           -> distributed
--   paso activo 'in_progress'       -> in_progress
--   registered con algún paso       -> distributed
--   cualquier otro                  -> se conserva
--
-- Cada cambio queda en `radicado_estado_recalculo`: tabla de SOLO INSERCIÓN
-- (trigger que rechaza UPDATE/DELETE, como `flow_events`) con el número de
-- radicado, el estado antes/después, el estado del paso activo que lo motivó y
-- `origen = 'migracion_013'`. Es la evidencia de una escritura masiva sobre un
-- campo con valor legal: debe poder leerse años después y no poder reescribirse.
--
-- El backfill NO toca `updated_at`: recalcular un estado derivado no es una edición
-- del radicado, y moverle la fecha de modificación a miles de documentos oficiales
-- falsea «cuándo se modificó por última vez» (la evidencia lleva su propio `at`).
--
-- NO se escribe `audit_log` desde SQL: la cadena de hash la firma la aplicación y
-- un asiento forjado desde aquí rompería su integridad. El asiento-ancla
-- (`workflow.estado_radicado.backfill`: conteo + SHA-256 del contenido ordenado de
-- esta tabla) lo escribe el RUNNER de migraciones (Python, `audit.append`) justo
-- después de aplicar este archivo, en la misma transacción — ver
-- `migrations/runner.py::anclar_backfill_013`.
--
-- Idempotente: la 2.ª ejecución no cambia ninguna fila (IS DISTINCT FROM) y no
-- añade evidencia. Si `radicados` aún no existe en este schema (tenant nuevo, el
-- document-service migra después) no hace nada: no hay datos que recalcular.
--
-- Rollback (la evidencia permite restaurar; el trigger se retira antes de borrar):
--   UPDATE radicados r SET status = e.antes FROM radicado_estado_recalculo e
--    WHERE e.radicado_id = r.id AND r.status = e.despues;
--   DROP TABLE radicado_estado_recalculo;

CREATE TABLE IF NOT EXISTS radicado_estado_recalculo (
    id              BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    radicado_id     UUID        NOT NULL,
    tracking_number VARCHAR(50) NOT NULL,
    antes           VARCHAR(20) NOT NULL,
    despues         VARCHAR(20) NOT NULL,
    paso_activo     VARCHAR(20),          -- estado del paso activo que motivó el cambio (NULL = sin paso activo)
    origen          VARCHAR(40) NOT NULL DEFAULT 'migracion_013',
    at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION reject_radicado_estado_recalculo_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'radicado_estado_recalculo is append-only (evidencia de la migración 013)';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_radicado_estado_recalculo_immutable ON radicado_estado_recalculo;
CREATE TRIGGER trg_radicado_estado_recalculo_immutable
    BEFORE UPDATE OR DELETE ON radicado_estado_recalculo
    FOR EACH ROW EXECUTE FUNCTION reject_radicado_estado_recalculo_mutation();

DO $$
DECLARE
    v_cambiados   INTEGER;
    v_terminales  INTEGER;
BEGIN
    IF to_regclass(format('%I.radicados', current_schema())) IS NULL THEN
        RAISE NOTICE 'radicados no existe en %: backfill de estado omitido', current_schema();
        RETURN;
    END IF;

    WITH activo AS (
        SELECT DISTINCT ON (radicado_id) radicado_id, status
          FROM flow_steps
         WHERE status IN ('pending', 'in_progress')
         ORDER BY radicado_id, step_number DESC
    ),
    con_pasos AS (
        SELECT DISTINCT radicado_id FROM flow_steps
    ),
    calc AS (
        SELECT r.id,
               r.tracking_number,
               r.status AS antes,
               a.status AS paso_activo,
               CASE
                   WHEN a.status = 'pending'     THEN 'distributed'
                   WHEN a.status = 'in_progress' THEN 'in_progress'
                   WHEN r.status = 'registered' AND cp.radicado_id IS NOT NULL
                                                 THEN 'distributed'
                   ELSE r.status
               END AS despues
          FROM radicados r
          LEFT JOIN activo    a  ON a.radicado_id  = r.id
          LEFT JOIN con_pasos cp ON cp.radicado_id = r.id
         WHERE r.status NOT IN ('closed', 'anulado')
    ),
    upd AS (
        UPDATE radicados r
           SET status = c.despues
          FROM calc c
         WHERE r.id = c.id
           AND r.status IS DISTINCT FROM c.despues
        RETURNING r.id, c.tracking_number, c.antes, c.despues, c.paso_activo
    )
    INSERT INTO radicado_estado_recalculo
           (radicado_id, tracking_number, antes, despues, paso_activo)
    SELECT id, tracking_number, antes, despues, paso_activo FROM upd;

    GET DIAGNOSTICS v_cambiados = ROW_COUNT;

    -- Terminales que aún tienen un paso activo (anulado/cerrado sin cancelar el
    -- paso). No se tocan aquí: los sanea la migración 015 con su propia evidencia.
    SELECT count(*) INTO v_terminales
      FROM radicados r
     WHERE r.status IN ('closed', 'anulado')
       AND EXISTS (SELECT 1 FROM flow_steps f
                    WHERE f.radicado_id = r.id
                      AND f.status IN ('pending', 'in_progress'));

    RAISE NOTICE 'backfill estado radicado (%): % recalculados, % terminales con paso activo',
        current_schema(), v_cambiados, v_terminales;
END $$;
