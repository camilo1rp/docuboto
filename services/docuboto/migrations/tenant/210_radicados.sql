-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Filed records, their numbering, actors, annulment and reclassification.

-- ── tracking_sequences ────────────────────────────────────────────────────────

CREATE TABLE tracking_sequences (
    year integer NOT NULL,
    doc_type character varying(1) NOT NULL,
    last_seq integer DEFAULT 0 NOT NULL,
    CONSTRAINT tracking_sequences_pkey PRIMARY KEY (year, doc_type)
);

-- ── radicados ─────────────────────────────────────────────────────────────────

CREATE TABLE radicados (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tracking_number character varying(50) NOT NULL,
    doc_type character varying(1) NOT NULL,
    year integer NOT NULL,
    sequence integer NOT NULL,
    subject text NOT NULL,
    doc_class character varying(100),
    origin_dept character varying(100),
    dest_dept character varying(100),
    sender_name character varying(255),
    sender_entity character varying(255),
    status character varying(20) DEFAULT 'registered'::character varying NOT NULL,
    pages integer DEFAULT 0 NOT NULL,
    observations text,
    registered_by uuid,
    registered_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    tenant_code character varying(20) NOT NULL,
    due_date date,
    origin_dept_code integer,
    dest_dept_code integer,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    metadata_template_id uuid,
    disposition jsonb,
    verification_code character varying(32) DEFAULT replace((gen_random_uuid())::text, '-'::text, ''::text) NOT NULL,
    responde_a uuid,
    nivel_seguridad smallint DEFAULT 1 NOT NULL,
    clasificacion_fundamento text,
    clasificacion_causal text,
    clasificacion_plazo_meses integer,
    clasificado_at timestamp with time zone,
    publicable boolean DEFAULT false NOT NULL,
    publicado_at timestamp with time zone,
    publicado_por uuid,
    search_vector tsvector GENERATED ALWAYS AS ((((((setweight(to_tsvector('public.spanish_unaccent'::regconfig, (COALESCE(tracking_number, ''::character varying))::text), 'A'::"char") || setweight(to_tsvector('public.spanish_unaccent'::regconfig, COALESCE(subject, ''::text)), 'B'::"char")) || setweight(to_tsvector('public.spanish_unaccent'::regconfig, (COALESCE(sender_name, ''::character varying))::text), 'C'::"char")) || setweight(to_tsvector('public.spanish_unaccent'::regconfig, (COALESCE(sender_entity, ''::character varying))::text), 'C'::"char")) || setweight(to_tsvector('public.spanish_unaccent'::regconfig, (COALESCE(dest_dept, ''::character varying))::text), 'D'::"char")) || setweight(to_tsvector('public.spanish_unaccent'::regconfig, COALESCE(observations, ''::text)), 'D'::"char"))) STORED,
    flujo_dept_code integer,
    flujo_dept character varying(100),
    flujo_usuario uuid,
    ubicacion_dept_code integer GENERATED ALWAYS AS (
CASE
    WHEN (flujo_dept IS NOT NULL) THEN flujo_dept_code
    WHEN ((dest_dept IS NOT NULL) OR (dest_dept_code IS NOT NULL)) THEN dest_dept_code
    ELSE origin_dept_code
END) STORED,
    ubicacion_dept character varying(100) GENERATED ALWAYS AS (
CASE
    WHEN (flujo_dept IS NOT NULL) THEN flujo_dept
    WHEN ((dest_dept IS NOT NULL) OR (dest_dept_code IS NOT NULL)) THEN dest_dept
    ELSE origin_dept
END) STORED,
    clasificacion_norma text,
    termino_inicial date,
    CONSTRAINT ck_radicados_nivel_seguridad CHECK (((nivel_seguridad >= 1) AND (nivel_seguridad <= 3))),
    CONSTRAINT radicados_causal_catalogo CHECK (COALESCE((((nivel_seguridad = 1) AND (clasificacion_causal IS NULL) AND (clasificacion_plazo_meses IS NULL)) OR ((nivel_seguridad >= 2) AND (clasificacion_causal = 'por_clasificar'::text)) OR ((nivel_seguridad = 3) AND (clasificacion_causal = ANY (ARRAY['art18_a_intimidad'::text, 'art18_b_vida_salud_seguridad'::text, 'art18_c_secretos'::text])) AND (clasificacion_plazo_meses IS NULL)) OR ((nivel_seguridad = 2) AND (clasificacion_causal = ANY (ARRAY['art19_a_defensa_seguridad_nacional'::text, 'art19_b_seguridad_publica'::text, 'art19_c_relaciones_internacionales'::text, 'art19_d_prevencion_investigacion_delitos'::text, 'art19_e_debido_proceso'::text, 'art19_f_administracion_justicia'::text, 'art19_g_infancia_adolescencia'::text, 'art19_h_estabilidad_macroeconomica'::text, 'art19_i_salud_publica'::text, 'art19_par_deliberativo'::text])) AND ((clasificacion_plazo_meses >= 1) AND (clasificacion_plazo_meses <= 180)))), false)),
    CONSTRAINT radicados_clasificado_con_fundamento CHECK (((nivel_seguridad < 2) OR ((clasificacion_fundamento IS NOT NULL) AND (btrim(clasificacion_fundamento) <> ''::text)))),
    CONSTRAINT radicados_norma_controlada CHECK (((clasificacion_norma IS NULL) OR ((nivel_seguridad >= 2) AND (clasificacion_norma ~ '^(Constitución Política|Ley|Decreto-Ley|Decreto|Acuerdo|Resolución|Código)[ A-Za-z0-9ÁÉÍÓÚÑáéíóúñ.,°º/()-]{1,150}$'::text) AND (clasificacion_norma ~ '[0-9]'::text)))),
    CONSTRAINT radicados_reserva_tope_15_anios CHECK (COALESCE(((nivel_seguridad <> 2) OR (clasificacion_causal = 'por_clasificar'::text) OR (clasificacion_plazo_meses IS NULL) OR (clasificacion_plazo_meses <= (180 - reserva_meses_entre(registered_at, COALESCE(clasificado_at, registered_at))))), false)),
    CONSTRAINT radicados_status_check CHECK ((status IN ('registered', 'distributed', 'in_progress', 'closed', 'anulado'))),
    CONSTRAINT radicados_pkey PRIMARY KEY (id),
    CONSTRAINT radicados_tracking_number_key UNIQUE (tracking_number),
    CONSTRAINT radicados_responde_a_fkey FOREIGN KEY (responde_a) REFERENCES radicados(id) ON DELETE SET NULL
);

COMMENT ON COLUMN radicados.publicable IS 'Esquema de publicación (Ley 1712/2014 art. 12): acto EXPLÍCITO por el que la entidad difunde el documento de oficio. FALSE por defecto — la ausencia de reserva no equivale a una decisión de publicar. Condición NECESARIA pero no suficiente: el recorte público exige además nivel_seguridad = 1 y no anulado.';

COMMENT ON COLUMN radicados.flujo_dept IS 'Nombre de la dependencia del paso de flujo vigente (o del último cerrado). La escribe SOLO workflow-service (ADR-029).';

COMMENT ON COLUMN radicados.ubicacion_dept IS 'Dónde está el radicado AHORA (generada): flujo_* si el flujo lo movió, si no la dependencia de radicación (ADR-029).';

COMMENT ON COLUMN radicados.termino_inicial IS 'Primera fecha de vencimiento. La corrección actualiza due_date y deja esta.';

CREATE INDEX ix_radicados_clasificados ON radicados USING btree (nivel_seguridad, registered_at DESC) WHERE (nivel_seguridad >= 2);
CREATE INDEX ix_radicados_dest_dept_code ON radicados USING btree (dest_dept_code);
CREATE INDEX ix_radicados_doc_type ON radicados USING btree (doc_type);
CREATE INDEX ix_radicados_due_date ON radicados USING btree (due_date);
CREATE INDEX ix_radicados_metadata_gin ON radicados USING gin (metadata);
CREATE INDEX ix_radicados_nivel_seguridad ON radicados USING btree (nivel_seguridad);
CREATE INDEX ix_radicados_origin_dept_code ON radicados USING btree (origin_dept_code);
CREATE INDEX ix_radicados_publicables ON radicados USING btree (registered_at) WHERE (publicable AND (nivel_seguridad = 1) AND ((status)::text <> 'anulado'::text));
CREATE INDEX ix_radicados_registered_at ON radicados USING btree (registered_at);
CREATE INDEX ix_radicados_responde_a ON radicados USING btree (responde_a);
CREATE INDEX ix_radicados_search_vector ON radicados USING gin (search_vector);
CREATE INDEX ix_radicados_sender_trgm ON radicados USING gin (sender_name public.gin_trgm_ops);
CREATE INDEX ix_radicados_status ON radicados USING btree (status);
CREATE INDEX ix_radicados_subject_trgm ON radicados USING gin (subject public.gin_trgm_ops);
CREATE INDEX ix_radicados_tracking_number ON radicados USING btree (tracking_number);
CREATE INDEX ix_radicados_tracking_trgm ON radicados USING gin (tracking_number public.gin_trgm_ops);
CREATE INDEX ix_radicados_ubicacion_dept_code ON radicados USING btree (ubicacion_dept_code);
CREATE UNIQUE INDEX ux_radicados_verification_code ON radicados USING btree (verification_code);

-- ── radicado_actor ────────────────────────────────────────────────────────────

CREATE TABLE radicado_actor (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    radicado_id uuid NOT NULL,
    tercero_id uuid NOT NULL,
    rol text NOT NULL,
    orden integer DEFAULT 0 NOT NULL,
    principal boolean DEFAULT false NOT NULL,
    nombre text NOT NULL,
    entidad text,
    email text,
    telefono text,
    direccion text,
    municipio text,
    departamento text,
    pais text,
    canal text,
    creado_at timestamp with time zone DEFAULT now() NOT NULL,
    departamento_codigo character(2),
    municipio_codigo character(5),
    CONSTRAINT radicado_actor_canal_check CHECK (((canal IS NULL) OR (canal = ANY (ARRAY['email'::text, 'fisico'::text, 'ambos'::text])))),
    CONSTRAINT radicado_actor_canal_email_check CHECK (((canal <> ALL (ARRAY['email'::text, 'ambos'::text])) OR (length(btrim(COALESCE(email, ''::text))) > 0))),
    CONSTRAINT radicado_actor_canal_fisico_check CHECK (((canal <> ALL (ARRAY['fisico'::text, 'ambos'::text])) OR (length(btrim(COALESCE(direccion, ''::text))) > 0))),
    CONSTRAINT radicado_actor_nombre_forma_check CHECK ((length(btrim(nombre)) >= 2)),
    CONSTRAINT radicado_actor_rol_check CHECK ((rol = ANY (ARRAY['remitente'::text, 'destinatario'::text]))),
    CONSTRAINT radicado_actor_pkey PRIMARY KEY (id),
    CONSTRAINT radicado_actor_radicado_id_fkey FOREIGN KEY (radicado_id) REFERENCES radicados(id) ON DELETE CASCADE,
    CONSTRAINT radicado_actor_tercero_id_fkey FOREIGN KEY (tercero_id) REFERENCES tercero(id)
);

COMMENT ON TABLE radicado_actor IS 'Actores de un radicado (E01, RF-RAD-05): identidad por referencia (tercero_id) + datos de contacto CONGELADOS del momento del envío. Los datos congelados son inmutables por trigger.';

COMMENT ON COLUMN radicado_actor.direccion IS 'La dirección REALMENTE USADA en este radicado. No se actualiza nunca: cambiarla daría información errónea sobre un envío ya hecho.';

COMMENT ON COLUMN radicado_actor.canal IS 'email | fisico | ambos | NULL (sin decidir). Cada canal exige su dato en la copia congelada: sin correo no hay envío electrónico, sin dirección no hay envío físico. Lo imponen dos CHECK, no la pantalla.';

COMMENT ON COLUMN radicado_actor.municipio_codigo IS 'Código DIVIPOLA CONGELADO del envío. SIN clave foránea a propósito: la copia debe sobrevivir a que el catálogo cambie o a que el municipio deje de existir.';

CREATE INDEX ix_radicado_actor_radicado ON radicado_actor USING btree (radicado_id, rol, orden);
CREATE INDEX ix_radicado_actor_tercero ON radicado_actor USING btree (tercero_id);
CREATE UNIQUE INDEX ux_radicado_actor_principal ON radicado_actor USING btree (radicado_id, rol) WHERE principal;
CREATE UNIQUE INDEX ux_radicado_actor_remitente ON radicado_actor USING btree (radicado_id) WHERE (rol = 'remitente'::text);
CREATE UNIQUE INDEX ux_radicado_actor_sin_duplicado ON radicado_actor USING btree (radicado_id, tercero_id, rol, COALESCE(direccion, ''::text), COALESCE(email, ''::text), COALESCE(municipio_codigo, ''::bpchar));

-- ── radicado_anulacion ────────────────────────────────────────────────────────

CREATE TABLE radicado_anulacion (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    radicado_id uuid NOT NULL,
    estado character varying(20) DEFAULT 'solicitada'::character varying NOT NULL,
    causal character varying(15) NOT NULL,
    motivo text,
    solicitado_por uuid,
    solicitado_at timestamp with time zone DEFAULT now() NOT NULL,
    decidido_por uuid,
    decidido_at timestamp with time zone,
    observacion text,
    CONSTRAINT radicado_anulacion_estado_check CHECK ((estado IN ('solicitada', 'aprobada', 'rechazada'))),
    CONSTRAINT radicado_anulacion_pkey PRIMARY KEY (id),
    CONSTRAINT radicado_anulacion_radicado_id_fkey FOREIGN KEY (radicado_id) REFERENCES radicados(id) ON DELETE CASCADE
);

CREATE INDEX ix_anulacion_radicado ON radicado_anulacion USING btree (radicado_id);
CREATE UNIQUE INDEX ux_anulacion_activa ON radicado_anulacion USING btree (radicado_id) WHERE ((estado)::text = 'solicitada'::text);

-- ── reclasificacion_solicitudes ───────────────────────────────────────────────

CREATE TABLE reclasificacion_solicitudes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    radicado_id uuid NOT NULL,
    nivel_anterior smallint NOT NULL,
    nivel_nuevo smallint NOT NULL,
    motivo text NOT NULL,
    causal_codigo text,
    fundamento_juridico text,
    plazo_meses integer,
    estado text DEFAULT 'pendiente'::text NOT NULL,
    solicitado_por uuid NOT NULL,
    solicitado_at timestamp with time zone DEFAULT now() NOT NULL,
    vence_at timestamp with time zone DEFAULT (now() + '15 days'::interval) NOT NULL,
    resuelto_por uuid,
    resuelto_at timestamp with time zone,
    resolucion_motivo text,
    fundamento_norma text,
    CONSTRAINT reclasificacion_solicitudes_check CHECK (((nivel_nuevo = ANY (ARRAY[1, 2])) AND (nivel_nuevo < nivel_anterior))),
    CONSTRAINT reclasificacion_solicitudes_estado_check CHECK ((estado = ANY (ARRAY['pendiente'::text, 'aprobada'::text, 'rechazada'::text, 'retirada'::text, 'caducada'::text]))),
    CONSTRAINT reclasificacion_solicitudes_motivo_check CHECK ((length(btrim(motivo)) >= 10)),
    CONSTRAINT reclasificacion_solicitudes_nivel_anterior_check CHECK ((nivel_anterior = 3)),
    CONSTRAINT rs_causal_destino CHECK (COALESCE((((nivel_nuevo = 1) AND (causal_codigo IS NULL) AND (plazo_meses IS NULL)) OR ((nivel_nuevo = 2) AND (causal_codigo = ANY (ARRAY['art19_a_defensa_seguridad_nacional'::text, 'art19_b_seguridad_publica'::text, 'art19_c_relaciones_internacionales'::text, 'art19_d_prevencion_investigacion_delitos'::text, 'art19_e_debido_proceso'::text, 'art19_f_administracion_justicia'::text, 'art19_g_infancia_adolescencia'::text, 'art19_h_estabilidad_macroeconomica'::text, 'art19_i_salud_publica'::text, 'art19_par_deliberativo'::text])) AND (fundamento_juridico IS NOT NULL) AND (btrim(fundamento_juridico) <> ''::text) AND ((plazo_meses >= 1) AND (plazo_meses <= 180)))), false)),
    CONSTRAINT rs_norma_controlada CHECK (((fundamento_norma IS NULL) OR ((nivel_nuevo = 2) AND (fundamento_norma ~ '^(Constitución Política|Ley|Decreto-Ley|Decreto|Acuerdo|Resolución|Código)[ A-Za-z0-9ÁÉÍÓÚÑáéíóúñ.,°º/()-]{1,150}$'::text) AND (fundamento_norma ~ '[0-9]'::text)))),
    CONSTRAINT rs_rechazo_motivado CHECK (((estado <> 'rechazada'::text) OR (length(btrim(COALESCE(resolucion_motivo, ''::text))) >= 10))),
    CONSTRAINT rs_resuelta_forma CHECK (((estado = ANY (ARRAY['pendiente'::text, 'caducada'::text])) OR ((resuelto_por IS NOT NULL) AND (resuelto_at IS NOT NULL)))),
    CONSTRAINT rs_retirada_por_solicitante CHECK (((estado <> 'retirada'::text) OR (resuelto_por = solicitado_por))),
    CONSTRAINT rs_separacion_funciones CHECK (((estado <> ALL (ARRAY['aprobada'::text, 'rechazada'::text])) OR (resuelto_por <> solicitado_por))),
    CONSTRAINT reclasificacion_solicitudes_pkey PRIMARY KEY (id),
    CONSTRAINT reclasificacion_solicitudes_radicado_id_fkey FOREIGN KEY (radicado_id) REFERENCES radicados(id) ON DELETE CASCADE
);

CREATE INDEX ix_rs_cola ON reclasificacion_solicitudes USING btree (solicitado_at) WHERE (estado = 'pendiente'::text);
CREATE INDEX ix_rs_radicado ON reclasificacion_solicitudes USING btree (radicado_id, solicitado_at DESC);
CREATE UNIQUE INDEX ux_rs_pendiente ON reclasificacion_solicitudes USING btree (radicado_id) WHERE (estado = 'pendiente'::text);

-- ── dependencia_codigo_backfill_doc ───────────────────────────────────────────

CREATE TABLE dependencia_codigo_backfill_doc (
    id bigint NOT NULL,
    tabla character varying(40) NOT NULL,
    fila_id uuid NOT NULL,
    columna character varying(40) NOT NULL,
    nombre text NOT NULL,
    codigo integer,
    candidatos integer NOT NULL,
    resultado character varying(10) NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    historico_id bigint,
    CONSTRAINT dependencia_codigo_backfill_doc_resultado_check CHECK ((resultado IN ('resuelto', 'ambiguo', 'huerfano'))),
    CONSTRAINT dependencia_codigo_backfill_doc_pkey PRIMARY KEY (id)
);

ALTER TABLE dependencia_codigo_backfill_doc ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME dependencia_codigo_backfill_doc_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE INDEX ix_dependencia_codigo_backfill_doc_fila ON dependencia_codigo_backfill_doc USING btree (tabla, fila_id, columna);

-- ── Functions ─────────────────────────────────────────────────────────────────

CREATE FUNCTION radicados_desclasificacion_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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
$$;

CREATE FUNCTION reclasificacion_solicitud_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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
$$;

CREATE FUNCTION reject_dependencia_codigo_backfill_doc_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'dependencia_codigo_backfill_doc is append-only (evidencia de la migración 046)';
END;
$$;

CREATE FUNCTION reject_radicado_actor_snapshot_update() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.nombre IS DISTINCT FROM OLD.nombre
       OR NEW.entidad IS DISTINCT FROM OLD.entidad
       OR NEW.email IS DISTINCT FROM OLD.email
       OR NEW.telefono IS DISTINCT FROM OLD.telefono
       OR NEW.direccion IS DISTINCT FROM OLD.direccion
       OR NEW.municipio IS DISTINCT FROM OLD.municipio
       OR NEW.departamento IS DISTINCT FROM OLD.departamento
       OR NEW.municipio_codigo IS DISTINCT FROM OLD.municipio_codigo
       OR NEW.departamento_codigo IS DISTINCT FROM OLD.departamento_codigo
       OR NEW.pais IS DISTINCT FROM OLD.pais
       OR NEW.tercero_id IS DISTINCT FROM OLD.tercero_id
    THEN
        RAISE EXCEPTION
            'los datos de contacto de un actor son los USADOS en el envío y no se editan '
            '(E01, RF-RAD-05): cambiarlos daría información errónea sobre un acto ya ocurrido';
    END IF;
    RETURN NEW;
END;
$$;

-- ── Triggers ──────────────────────────────────────────────────────────────────

CREATE TRIGGER trg_radicados_desclasificacion BEFORE UPDATE OF nivel_seguridad ON radicados FOR EACH ROW EXECUTE FUNCTION radicados_desclasificacion_guard();

CREATE TRIGGER trg_radicado_actor_snapshot BEFORE UPDATE ON radicado_actor FOR EACH ROW EXECUTE FUNCTION reject_radicado_actor_snapshot_update();

CREATE TRIGGER trg_reclasificacion_solicitud_guard BEFORE DELETE OR UPDATE ON reclasificacion_solicitudes FOR EACH ROW EXECUTE FUNCTION reclasificacion_solicitud_guard();

CREATE TRIGGER trg_dependencia_codigo_backfill_doc_immutable BEFORE DELETE OR UPDATE ON dependencia_codigo_backfill_doc FOR EACH ROW EXECUTE FUNCTION reject_dependencia_codigo_backfill_doc_mutation();
