-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (ADR-029): ubicación actual del radicado desde el flujo.
--
-- Rellena `radicados.flujo_dept` / `flujo_dept_code` / `flujo_usuario` (columnas
-- que crea la migración 037 de document-service) para los radicados existentes,
-- con el gemelo LITERAL de `app/services/estado_radicado.py::derivar_ubicacion`
-- (la prueba de integración compara cada fila con la función Python):
--
--   paso activo (pending|in_progress)  -> el de mayor step_number
--   sin paso activo                    -> el que se cerró más reciente
--                                         (completed_at DESC, step_number DESC),
--                                         en CUALQUIER estado
--   sin pasos                          -> no se escribe (la lectura cae a la
--                                         dependencia de radicación)
--   código                             -> solo si el NOMBRE casa con exactamente
--                                         UNA dependencia activa; si no, NULL y
--                                         el nombre queda como instantánea
--   terminal (closed|anulado)          -> se rellena solo si NUNCA se escribió;
--                                         si ya tiene ubicación, no se toca
--
-- Cada cambio queda en `radicado_ubicacion_recalculo`: tabla de SOLO INSERCIÓN
-- (trigger que rechaza UPDATE/DELETE) con el antes/después y
-- `origen = 'migracion_016'`. NO se escribe `audit_log` desde SQL: el asiento-ancla
-- (`workflow.ubicacion_radicado.backfill`: conteo + SHA-256 del contenido ordenado)
-- lo escribe el RUNNER (`migrations/runner.py::_ANCLAS`), en la misma transacción.
-- El backfill NO toca `updated_at`: recalcular un dato derivado no es editar el
-- documento.
--
-- Guardas de ORDEN (la migración se marca aplicada al terminar, así que un `skip`
-- silencioso donde hay datos dejaría la entidad sin backfill para siempre):
--   * `radicados` no existe en este schema -> no hay datos que recalcular: omite.
--   * `radicados` existe pero SIN las columnas (el runner de document-service no
--     corrió) -> RAISE: falla ruidosa, se reintenta tras migrar document-service.
--
-- Idempotente: la 2.ª ejecución no cambia ninguna fila (IS DISTINCT FROM).
--
-- Rollback (la evidencia permite restaurar; se retira el trigger antes de borrar):
--   UPDATE radicados r SET flujo_dept = e.antes_dept, flujo_dept_code = e.antes_dept_code,
--          flujo_usuario = e.antes_usuario
--     FROM radicado_ubicacion_recalculo e WHERE e.radicado_id = r.id;
--   DROP TABLE radicado_ubicacion_recalculo;

CREATE TABLE IF NOT EXISTS radicado_ubicacion_recalculo (
    id                BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    radicado_id       UUID        NOT NULL,
    tracking_number   VARCHAR(50) NOT NULL,
    estado_radicado   VARCHAR(20) NOT NULL,
    antes_dept        VARCHAR(100),
    antes_dept_code   INTEGER,
    antes_usuario     UUID,
    despues_dept      VARCHAR(100),
    despues_dept_code INTEGER,
    despues_usuario   UUID,
    paso_id           UUID,                -- paso que motivó la ubicación (NULL = ninguno)
    origen            VARCHAR(40) NOT NULL DEFAULT 'migracion_016',
    at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION reject_radicado_ubicacion_recalculo_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'radicado_ubicacion_recalculo is append-only (evidencia de la migración 016)';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_radicado_ubicacion_recalculo_immutable ON radicado_ubicacion_recalculo;
CREATE TRIGGER trg_radicado_ubicacion_recalculo_immutable
    BEFORE UPDATE OR DELETE ON radicado_ubicacion_recalculo
    FOR EACH ROW EXECUTE FUNCTION reject_radicado_ubicacion_recalculo_mutation();

DO $$
DECLARE
    v_cambiados  INTEGER;
    v_sin_codigo INTEGER;
BEGIN
    IF to_regclass(format('%I.radicados', current_schema())) IS NULL THEN
        RAISE NOTICE 'radicados no existe en %: backfill de ubicación omitido', current_schema();
        RETURN;
    END IF;

    IF (SELECT count(*) FROM information_schema.columns
         WHERE table_schema = current_schema() AND table_name = 'radicados'
           AND column_name IN ('flujo_dept', 'flujo_dept_code', 'flujo_usuario')) < 3 THEN
        RAISE EXCEPTION
            'radicados de % no tiene las columnas flujo_* (migración 037 de document-service): '
            'migre document-service antes que workflow-service', current_schema();
    END IF;

    WITH elegido AS (
        SELECT DISTINCT ON (s.radicado_id)
               s.radicado_id, s.id AS paso_id, s.to_dept, s.assigned_to
          FROM flow_steps s
         ORDER BY s.radicado_id,
                  (s.status IN ('pending', 'in_progress')) DESC,
                  CASE WHEN s.status IN ('pending', 'in_progress') THEN NULL
                       ELSE s.completed_at END DESC NULLS LAST,
                  s.step_number DESC
    ),
    calc AS (
        SELECT r.id, r.tracking_number, r.status,
               r.flujo_dept AS antes_dept, r.flujo_dept_code AS antes_dept_code,
               r.flujo_usuario AS antes_usuario,
               e.to_dept AS despues_dept,
               (SELECT CASE WHEN count(*) = 1 THEN min(d.codigo::integer) END
                  FROM dependencias d
                 WHERE d.nombre = e.to_dept AND d.activo = TRUE) AS despues_dept_code,
               e.assigned_to AS despues_usuario,
               e.paso_id
          FROM radicados r
          LEFT JOIN elegido e ON e.radicado_id = r.id
         WHERE r.status NOT IN ('closed', 'anulado') OR r.flujo_dept IS NULL
    ),
    upd AS (
        UPDATE radicados r
           SET flujo_dept = c.despues_dept,
               flujo_dept_code = c.despues_dept_code,
               flujo_usuario = c.despues_usuario
          FROM calc c
         WHERE r.id = c.id
           AND (r.flujo_dept IS DISTINCT FROM c.despues_dept
                OR r.flujo_dept_code IS DISTINCT FROM c.despues_dept_code
                OR r.flujo_usuario IS DISTINCT FROM c.despues_usuario)
        RETURNING r.id, c.tracking_number, c.status,
                  c.antes_dept, c.antes_dept_code, c.antes_usuario,
                  c.despues_dept, c.despues_dept_code, c.despues_usuario, c.paso_id
    )
    INSERT INTO radicado_ubicacion_recalculo
           (radicado_id, tracking_number, estado_radicado,
            antes_dept, antes_dept_code, antes_usuario,
            despues_dept, despues_dept_code, despues_usuario, paso_id)
    SELECT id, tracking_number, status,
           antes_dept, antes_dept_code, antes_usuario,
           despues_dept, despues_dept_code, despues_usuario, paso_id
      FROM upd;

    GET DIAGNOSTICS v_cambiados = ROW_COUNT;

    SELECT count(*) INTO v_sin_codigo
      FROM radicados WHERE flujo_dept IS NOT NULL AND flujo_dept_code IS NULL;

    RAISE NOTICE 'backfill ubicación radicado (%): % recalculados, % con nombre sin código único',
        current_schema(), v_cambiados, v_sin_codigo;
END $$;
