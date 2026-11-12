-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (B5, ADR-032): el flujo correlaciona la dependencia por CÓDIGO.
--
-- Hasta aquí `flow_steps.to_dept`, `workflow_rules.assign_to_dept` y la bandeja
-- (`to_dept IN (SELECT d.nombre ...)`) se unían a `dependencias` por NOMBRE: renombrar
-- una dependencia con trámite vivo dejaba los pasos fuera de toda bandeja, y dos
-- homónimas se confundían. Esta migración añade el CÓDIGO (`dependencias.codigo`,
-- inmutable desde la 013 de tenant-service) y deja el nombre como INSTANTÁNEA legible.
--
--   A. DDL aditivo: `*_code` en flow_steps, flow_events y workflow_rules + tabla de
--      evidencia `dependencia_codigo_backfill` (solo inserción).
--   B. Backfill por nombre con `resolver_codigo_dependencia(nombre, true)` (013 de
--      tenant-service: UNA definición del criterio; incluye inactivas, nombres
--      anteriores y mapeos manuales). Solo se escribe con EXACTAMENTE un candidato.
--      NO se inventa:
--        - pasos VIVOS (to_dept), reglas (assign_to_dept, dest_dept) y condiciones
--          eq/ne/in sobre dest_dept/origin_dept con nombre ambiguo o huérfano ->
--          RAISE listando los primeros 20: la transacción entera se revierte y la
--          migración NO queda en el libro. Remedio: un administrador registra el
--          mapeo (`POST /dependencias/{id}/mapeos-nombre`) y se reejecuta.
--        - pasos terminales, from_dept y flow_events -> código NULL + evidencia
--          (historia: no se adivina).
--      Las condiciones textuales (contains/not_contains/regex) sobre un nombre se
--      CONSERVAN (semántica de texto legítima) y se listan en la evidencia como
--      `conditions:textual` para que el administrador las vea.
--   C. Ubicación (ADR-029): `radicados.flujo_dept_code` <- `to_dept_code` del paso
--      elegido con la MISMA regla de la 016 (gemelo de `derivar_ubicacion`).
--   D. Restricciones e índices: un paso VIVO sin código no puede existir; una regla
--      sin código destino tampoco.
--
-- `flow_events` es append-only (trigger). Esta migración completa SOLO las columnas
-- nuevas `from_dept_code`/`to_dept_code` (NULL -> código): desactiva el trigger dentro
-- de la transacción, escribe, lo reactiva y VERIFICA que quedó habilitado. El texto
-- del evento no se toca; cada fila escrita queda en la evidencia.
--
-- Guarda de ORDEN: la función viene de tenant-service 013. Si falta y HAY filas por
-- resolver -> RAISE (no un skip silencioso: el runner marcaría la migración aplicada
-- sin backfill, el mismo criterio de la 016). Sin filas pendientes no hay nada que perder.
--
-- El asiento-ANCLA en `audit_log` lo escribe el RUNNER (`_ANCLAS`), no el SQL.
-- Idempotente: la 2.ª ejecución no cambia filas (IS NULL / IS DISTINCT FROM), no
-- duplica evidencia (NOT EXISTS) ni ancla.
--
-- Rollback: las columnas nuevas se pueden `DROP`; los nombres nunca se tocaron.

-- A. DDL aditivo ---------------------------------------------------------------
ALTER TABLE flow_steps
    ADD COLUMN IF NOT EXISTS to_dept_code   INTEGER,
    ADD COLUMN IF NOT EXISTS from_dept_code INTEGER;
ALTER TABLE flow_events
    ADD COLUMN IF NOT EXISTS to_dept_code   INTEGER,
    ADD COLUMN IF NOT EXISTS from_dept_code INTEGER;
ALTER TABLE workflow_rules
    ADD COLUMN IF NOT EXISTS assign_to_dept_code INTEGER,
    ADD COLUMN IF NOT EXISTS dest_dept_code      INTEGER;

CREATE TABLE IF NOT EXISTS dependencia_codigo_backfill (
    id         BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tabla      VARCHAR(40) NOT NULL,
    fila_id    UUID        NOT NULL,
    columna    VARCHAR(60) NOT NULL,
    nombre     TEXT        NOT NULL,
    codigo     INTEGER,
    candidatos INTEGER     NOT NULL,
    resultado  VARCHAR(10) NOT NULL CHECK (resultado IN ('resuelto', 'mapeado', 'ambiguo', 'huerfano')),
    vivo       BOOLEAN     NOT NULL,
    at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION reject_dependencia_codigo_backfill_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'dependencia_codigo_backfill is append-only (evidencia de la migración 019)';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_dependencia_codigo_backfill_immutable ON dependencia_codigo_backfill;
CREATE TRIGGER trg_dependencia_codigo_backfill_immutable
    BEFORE UPDATE OR DELETE ON dependencia_codigo_backfill
    FOR EACH ROW EXECUTE FUNCTION reject_dependencia_codigo_backfill_mutation();

CREATE INDEX IF NOT EXISTS ix_dependencia_codigo_backfill_fila
    ON dependencia_codigo_backfill (tabla, fila_id, columna);

-- B. Backfill por nombre -----------------------------------------------------------
DO $$
DECLARE
    v_pend    INTEGER;
    v_cond    INTEGER;
    v_fallan  TEXT;
    v_rec     RECORD;
    v_elem    JSONB;
    v_nuevo   JSONB;
    v_nombres TEXT[];
    v_nombre  TEXT;
    v_codigos JSONB;
    v_res     RECORD;
    v_malos   TEXT := '';
    v_op      TEXT;
    v_campo   TEXT;
BEGIN
    DROP TABLE IF EXISTS _b5_pend, _b5_res;
    CREATE TEMP TABLE _b5_pend (
        tabla TEXT, fila_id UUID, columna TEXT, nombre TEXT, vivo BOOLEAN, obligatorio BOOLEAN
    ) ON COMMIT DROP;

    INSERT INTO _b5_pend
    SELECT 'flow_steps', id, 'to_dept', to_dept::text,
           status IN ('pending', 'in_progress'), status IN ('pending', 'in_progress')
      FROM flow_steps WHERE to_dept_code IS NULL AND to_dept IS NOT NULL
    UNION ALL
    SELECT 'flow_steps', id, 'from_dept', from_dept::text, status IN ('pending', 'in_progress'), FALSE
      FROM flow_steps WHERE from_dept_code IS NULL AND from_dept IS NOT NULL
    UNION ALL
    SELECT 'flow_events', id, 'to_dept', to_dept::text, FALSE, FALSE
      FROM flow_events WHERE to_dept_code IS NULL AND to_dept IS NOT NULL
    UNION ALL
    SELECT 'flow_events', id, 'from_dept', from_dept::text, FALSE, FALSE
      FROM flow_events WHERE from_dept_code IS NULL AND from_dept IS NOT NULL
    UNION ALL
    SELECT 'workflow_rules', id, 'assign_to_dept', assign_to_dept::text, TRUE, TRUE
      FROM workflow_rules WHERE assign_to_dept_code IS NULL AND assign_to_dept IS NOT NULL
    UNION ALL
    SELECT 'workflow_rules', id, 'dest_dept', dest_dept::text, TRUE, TRUE
      FROM workflow_rules WHERE dest_dept_code IS NULL AND dest_dept IS NOT NULL;

    SELECT count(*) INTO v_pend FROM _b5_pend;
    SELECT count(*) INTO v_cond
      FROM workflow_rules w
     WHERE jsonb_typeof(w.conditions) = 'array'
       AND EXISTS (SELECT 1 FROM jsonb_array_elements(w.conditions) e
                    WHERE e->>'field' IN ('dest_dept', 'origin_dept')
                      AND e->>'op' IN ('eq', 'ne', 'in', 'contains', 'not_contains', 'regex'));

    IF v_pend = 0 AND v_cond = 0 THEN
        RETURN;
    END IF;

    IF to_regprocedure(format('%I.resolver_codigo_dependencia(text,boolean)', current_schema())) IS NULL THEN
        RAISE EXCEPTION
            '% tiene % fila(s) con nombre de dependencia sin código y no existe '
            'resolver_codigo_dependencia (migración 013 de tenant-service): migre '
            'tenant-service antes que workflow-service', current_schema(), v_pend + v_cond;
    END IF;

    CREATE TEMP TABLE _b5_res ON COMMIT DROP AS
    SELECT p.*, x.codigo, x.candidatos
      FROM _b5_pend p
     CROSS JOIN LATERAL resolver_codigo_dependencia(p.nombre, TRUE) x;

    -- Lo obligatorio que no resuelve: falla CERRADO, listando los 20 primeros.
    SELECT string_agg(format('%s.%s id=%s «%s» (%s candidato(s))',
                             tabla, columna, fila_id, nombre, candidatos), E'\n')
      INTO v_fallan
      FROM (SELECT * FROM _b5_res WHERE obligatorio AND candidatos <> 1
             ORDER BY tabla, fila_id, columna LIMIT 20) f;
    IF v_fallan IS NOT NULL THEN
        RAISE EXCEPTION 'B5: nombres de dependencia ambiguos o inexistentes en trámite vivo o reglas; no se inventa un destino. Registre el mapeo (POST /dependencias/{id}/mapeos-nombre) y reejecute la migración:%',
            E'\n' || v_fallan;
    END IF;

    -- Condiciones eq/ne/in sobre dest_dept/origin_dept -> *_code (todas o RAISE).
    FOR v_rec IN
        SELECT w.id, w.conditions FROM workflow_rules w
         WHERE jsonb_typeof(w.conditions) = 'array'
           AND EXISTS (SELECT 1 FROM jsonb_array_elements(w.conditions) e
                        WHERE e->>'field' IN ('dest_dept', 'origin_dept')
                          AND e->>'op' IN ('eq', 'ne', 'in'))
         ORDER BY w.id
    LOOP
        v_nuevo := '[]'::jsonb;
        FOR v_elem IN SELECT value FROM jsonb_array_elements(v_rec.conditions) WITH ORDINALITY ORDER BY ordinality
        LOOP
            v_campo := v_elem->>'field';
            v_op    := v_elem->>'op';
            IF v_campo IN ('dest_dept', 'origin_dept') AND v_op IN ('eq', 'ne', 'in') THEN
                IF v_op = 'in' AND jsonb_typeof(v_elem->'value') = 'array' THEN
                    SELECT array_agg(t) INTO v_nombres FROM jsonb_array_elements_text(v_elem->'value') t;
                ELSIF v_op = 'in' THEN
                    -- el motor admite "a,b,c" como lista
                    SELECT array_agg(btrim(t)) INTO v_nombres
                      FROM unnest(string_to_array(v_elem->>'value', ',')) t WHERE btrim(t) <> '';
                ELSE
                    v_nombres := ARRAY[v_elem->>'value'];
                END IF;
                v_codigos := '[]'::jsonb;
                FOREACH v_nombre IN ARRAY COALESCE(v_nombres, ARRAY[]::text[]) LOOP
                    SELECT * INTO v_res FROM resolver_codigo_dependencia(v_nombre, TRUE);
                    IF v_res.candidatos <> 1 THEN
                        v_malos := v_malos || format(E'\nworkflow_rules id=%s conditions «%s» (%s candidato(s))',
                                                     v_rec.id, v_nombre, v_res.candidatos);
                    ELSE
                        v_codigos := v_codigos || to_jsonb(v_res.codigo);
                    END IF;
                END LOOP;
                v_elem := jsonb_build_object(
                    'field', v_campo || '_code',
                    'op', v_op,
                    'value', CASE WHEN v_op = 'in' THEN v_codigos ELSE COALESCE(v_codigos->0, 'null'::jsonb) END);
            END IF;
            v_nuevo := v_nuevo || jsonb_build_array(v_elem);
        END LOOP;
        UPDATE workflow_rules SET conditions = v_nuevo WHERE id = v_rec.id;
        INSERT INTO dependencia_codigo_backfill
               (tabla, fila_id, columna, nombre, codigo, candidatos, resultado, vivo)
        SELECT 'workflow_rules', v_rec.id, 'conditions:code', left(v_rec.conditions::text, 2000),
               NULL, 1, 'resuelto', TRUE
         WHERE NOT EXISTS (SELECT 1 FROM dependencia_codigo_backfill e
                            WHERE e.tabla = 'workflow_rules' AND e.fila_id = v_rec.id
                              AND e.columna = 'conditions:code');
    END LOOP;
    IF v_malos <> '' THEN
        RAISE EXCEPTION 'B5: condiciones por nombre de dependencia ambiguas o inexistentes; no se inventa un destino. Registre el mapeo y reejecute la migración:%', v_malos;
    END IF;

    -- Condiciones textuales: se conservan, pero quedan a la vista del administrador.
    INSERT INTO dependencia_codigo_backfill
           (tabla, fila_id, columna, nombre, codigo, candidatos, resultado, vivo)
    SELECT 'workflow_rules', w.id, 'conditions:textual', left(w.conditions::text, 2000),
           NULL, 1, 'resuelto', TRUE
      FROM workflow_rules w
     WHERE jsonb_typeof(w.conditions) = 'array'
       AND EXISTS (SELECT 1 FROM jsonb_array_elements(w.conditions) e
                    WHERE e->>'field' IN ('dest_dept', 'origin_dept')
                      AND e->>'op' IN ('contains', 'not_contains', 'regex'))
       AND NOT EXISTS (SELECT 1 FROM dependencia_codigo_backfill e
                        WHERE e.tabla = 'workflow_rules' AND e.fila_id = w.id
                          AND e.columna = 'conditions:textual')
     ORDER BY w.id;

    -- Escritura: SOLO lo resuelto con un único candidato.
    UPDATE flow_steps s SET to_dept_code = r.codigo
      FROM _b5_res r
     WHERE r.tabla = 'flow_steps' AND r.columna = 'to_dept' AND r.candidatos = 1
       AND r.fila_id = s.id AND s.to_dept_code IS NULL;
    UPDATE flow_steps s SET from_dept_code = r.codigo
      FROM _b5_res r
     WHERE r.tabla = 'flow_steps' AND r.columna = 'from_dept' AND r.candidatos = 1
       AND r.fila_id = s.id AND s.from_dept_code IS NULL;
    UPDATE workflow_rules w SET assign_to_dept_code = r.codigo
      FROM _b5_res r
     WHERE r.tabla = 'workflow_rules' AND r.columna = 'assign_to_dept' AND r.candidatos = 1
       AND r.fila_id = w.id AND w.assign_to_dept_code IS NULL;
    UPDATE workflow_rules w SET dest_dept_code = r.codigo
      FROM _b5_res r
     WHERE r.tabla = 'workflow_rules' AND r.columna = 'dest_dept' AND r.candidatos = 1
       AND r.fila_id = w.id AND w.dest_dept_code IS NULL;

    -- flow_events es append-only: solo las dos columnas nuevas, con el trigger apartado
    -- DENTRO de esta transacción y verificado al volver a habilitarlo.
    IF EXISTS (SELECT 1 FROM _b5_res WHERE tabla = 'flow_events' AND candidatos = 1) THEN
        ALTER TABLE flow_events DISABLE TRIGGER trg_flow_events_immutable;
        UPDATE flow_events e SET to_dept_code = r.codigo
          FROM _b5_res r
         WHERE r.tabla = 'flow_events' AND r.columna = 'to_dept' AND r.candidatos = 1
           AND r.fila_id = e.id AND e.to_dept_code IS NULL;
        UPDATE flow_events e SET from_dept_code = r.codigo
          FROM _b5_res r
         WHERE r.tabla = 'flow_events' AND r.columna = 'from_dept' AND r.candidatos = 1
           AND r.fila_id = e.id AND e.from_dept_code IS NULL;
        ALTER TABLE flow_events ENABLE TRIGGER trg_flow_events_immutable;
        IF NOT EXISTS (SELECT 1 FROM pg_trigger
                        WHERE tgrelid = 'flow_events'::regclass
                          AND tgname = 'trg_flow_events_immutable' AND tgenabled = 'O') THEN
            RAISE EXCEPTION 'trg_flow_events_immutable no quedó habilitado tras el backfill';
        END IF;
    END IF;

    -- Evidencia de cada nombre resuelto (o no), una vez por (tabla, fila, columna).
    INSERT INTO dependencia_codigo_backfill
           (tabla, fila_id, columna, nombre, codigo, candidatos, resultado, vivo)
    SELECT r.tabla, r.fila_id, r.columna, r.nombre, r.codigo, r.candidatos,
           CASE WHEN r.candidatos = 1 AND EXISTS (
                        SELECT 1 FROM dependencia_nombre_historico h
                         WHERE h.nombre = r.nombre AND h.origen = 'mapeo_manual') THEN 'mapeado'
                WHEN r.candidatos = 1 THEN 'resuelto'
                WHEN r.candidatos = 0 THEN 'huerfano'
                ELSE 'ambiguo' END,
           r.vivo
      FROM _b5_res r
     WHERE NOT EXISTS (SELECT 1 FROM dependencia_codigo_backfill e
                        WHERE e.tabla = r.tabla AND e.fila_id = r.fila_id AND e.columna = r.columna)
     ORDER BY r.tabla, r.fila_id, r.columna;

    RAISE NOTICE 'backfill dependencia por código (%): % nombres, % resueltos, % sin código único',
        current_schema(), v_pend,
        (SELECT count(*) FROM _b5_res WHERE candidatos = 1),
        (SELECT count(*) FROM _b5_res WHERE candidatos <> 1);
END $$;

-- C. Ubicación del radicado (ADR-029) ------------------------------------------------
DO $$
DECLARE
    v_cambiados INTEGER;
BEGIN
    IF to_regclass(format('%I.radicados', current_schema())) IS NULL THEN
        RAISE NOTICE 'radicados no existe en %: ubicación por código omitida', current_schema();
        RETURN;
    END IF;
    IF (SELECT count(*) FROM information_schema.columns
         WHERE table_schema = current_schema() AND table_name = 'radicados'
           AND column_name IN ('flujo_dept', 'flujo_dept_code', 'flujo_usuario')) < 3 THEN
        RAISE EXCEPTION
            'radicados de % no tiene las columnas flujo_* (migración 037 de document-service): '
            'migre document-service antes que workflow-service', current_schema();
    END IF;
    IF to_regclass(format('%I.radicado_ubicacion_recalculo', current_schema())) IS NULL THEN
        RAISE EXCEPTION 'falta radicado_ubicacion_recalculo (migración 016) en %', current_schema();
    END IF;

    -- Gemelo de la 016 y de `estado_radicado.derivar_ubicacion`: paso activo de mayor
    -- step_number; si no hay, el que se cerró más reciente. El código sale del PASO.
    WITH elegido AS (
        SELECT DISTINCT ON (s.radicado_id)
               s.radicado_id, s.id AS paso_id, s.to_dept, s.to_dept_code, s.assigned_to
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
               e.to_dept AS despues_dept, e.to_dept_code AS despues_dept_code,
               e.assigned_to AS despues_usuario, e.paso_id
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
            despues_dept, despues_dept_code, despues_usuario, paso_id, origen)
    SELECT id, tracking_number, status,
           antes_dept, antes_dept_code, antes_usuario,
           despues_dept, despues_dept_code, despues_usuario, paso_id, 'migracion_019'
      FROM upd
     ORDER BY id;

    GET DIAGNOSTICS v_cambiados = ROW_COUNT;
    RAISE NOTICE 'ubicación por código (%): % radicados recalculados', current_schema(), v_cambiados;
END $$;

-- D. Restricciones e índices ------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace
                    WHERE n.nspname = current_schema() AND c.conname = 'ck_flow_steps_vivo_con_codigo') THEN
        ALTER TABLE flow_steps ADD CONSTRAINT ck_flow_steps_vivo_con_codigo
            CHECK (to_dept_code IS NOT NULL OR status NOT IN ('pending', 'in_progress'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace
                    WHERE n.nspname = current_schema() AND c.conname = 'ck_workflow_rules_assign_code') THEN
        ALTER TABLE workflow_rules ADD CONSTRAINT ck_workflow_rules_assign_code
            CHECK (assign_to_dept_code IS NOT NULL);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace
                    WHERE n.nspname = current_schema() AND c.conname = 'ck_workflow_rules_dest_code') THEN
        ALTER TABLE workflow_rules ADD CONSTRAINT ck_workflow_rules_dest_code
            CHECK (dest_dept IS NULL OR dest_dept_code IS NOT NULL);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_flow_steps_to_dept_code_status_doc_type
    ON flow_steps (to_dept_code, status, doc_type);
CREATE INDEX IF NOT EXISTS ix_flow_steps_to_dept_code ON flow_steps (to_dept_code);
CREATE INDEX IF NOT EXISTS ix_flow_events_to_dept_code ON flow_events (to_dept_code);

COMMENT ON COLUMN flow_steps.to_dept_code IS
    'Código (dependencias.codigo) del destino del paso: la LLAVE de tenencia y bandeja (ADR-032). to_dept es la instantánea del nombre.';
