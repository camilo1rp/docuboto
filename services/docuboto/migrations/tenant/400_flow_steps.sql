-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Workflow: transaction types, steps, events and approvals.

-- ── transaction_types ─────────────────────────────────────────────────────────

CREATE TABLE transaction_types (
    code character varying(40) NOT NULL,
    nombre text NOT NULL,
    descripcion text,
    requires_permission text,
    target_step_status character varying(20),
    activo boolean DEFAULT true NOT NULL,
    requires_holder boolean DEFAULT false NOT NULL,
    CONSTRAINT transaction_types_pkey PRIMARY KEY (code)
);

INSERT INTO transaction_types (code, nombre, descripcion, requires_permission, target_step_status, activo, requires_holder) VALUES
    ('agendar', 'Agendar', NULL, 'PERM_AGENDAR', NULL, 'true', 'false'),
    ('anular', 'Anular', NULL, 'PERM_ANULAR', 'cancelled', 'true', 'true'),
    ('borrar_informado', 'Borrar informado', NULL, 'PERM_INFORMAR', NULL, 'true', 'false'),
    ('cerrar_exp', 'Cerrar expediente', NULL, 'PERM_CERRAR_EXP', NULL, 'true', 'false'),
    ('change_folder', 'Cambiar de carpeta', NULL, 'PERM_TRAMITAR', NULL, 'true', 'false'),
    ('informar', 'Informar (copia)', NULL, 'PERM_INFORMAR', NULL, 'true', 'false'),
    ('marcar_leido', 'Marcar como leído', NULL, 'PERM_TRAMITAR', 'in_progress', 'true', 'true'),
    ('no_agendar', 'Quitar de agenda', NULL, 'PERM_AGENDAR', NULL, 'true', 'false'),
    ('nrr', 'No requiere respuesta', NULL, 'PERM_TRAMITAR', NULL, 'true', 'false'),
    ('solicitar_firma', 'Solicitar firma', NULL, 'PERM_FIRMA', NULL, 'true', 'false'),
    ('validate_trd_send', 'Validar TRD para envío', NULL, 'PERM_TRAMITAR', NULL, 'true', 'false'),
    ('vobo', 'Visto bueno', NULL, 'PERM_VOBO', NULL, 'true', 'false');

-- ── flow_steps ────────────────────────────────────────────────────────────────

CREATE TABLE flow_steps (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    radicado_id uuid NOT NULL,
    tracking_number character varying(50) NOT NULL,
    step_number integer NOT NULL,
    from_dept character varying(100),
    to_dept character varying(100) NOT NULL,
    assigned_to uuid,
    assigned_by uuid NOT NULL,
    action character varying(30) NOT NULL,
    notes text,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    doc_type character varying(1),
    due_at timestamp with time zone,
    overdue_notified boolean DEFAULT false NOT NULL,
    to_dept_code integer,
    from_dept_code integer,
    CONSTRAINT ck_flow_steps_vivo_con_codigo CHECK (((to_dept_code IS NOT NULL) OR (status NOT IN ('pending', 'in_progress')))),
    CONSTRAINT flow_steps_pkey PRIMARY KEY (id)
);

COMMENT ON COLUMN flow_steps.to_dept_code IS 'Código (dependencias.codigo) del destino del paso: la LLAVE de tenencia y bandeja (ADR-032). to_dept es la instantánea del nombre.';

CREATE INDEX ix_flow_steps_assigned_to ON flow_steps USING btree (assigned_to);
CREATE INDEX ix_flow_steps_due ON flow_steps USING btree (status, due_at) WHERE (due_at IS NOT NULL);
CREATE INDEX ix_flow_steps_inbox ON flow_steps USING btree (to_dept, status, doc_type);
CREATE INDEX ix_flow_steps_radicado_id ON flow_steps USING btree (radicado_id);
CREATE INDEX ix_flow_steps_status ON flow_steps USING btree (status);
CREATE INDEX ix_flow_steps_to_dept ON flow_steps USING btree (to_dept);
CREATE INDEX ix_flow_steps_to_dept_code ON flow_steps USING btree (to_dept_code);
CREATE INDEX ix_flow_steps_to_dept_code_status_doc_type ON flow_steps USING btree (to_dept_code, status, doc_type);
CREATE INDEX ix_flow_steps_tracking_number ON flow_steps USING btree (tracking_number);

-- ── flow_events ───────────────────────────────────────────────────────────────

CREATE TABLE flow_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    radicado_id uuid NOT NULL,
    tracking_number character varying(50) NOT NULL,
    tipo_tx character varying(40) NOT NULL,
    from_dept text,
    to_dept text,
    actor uuid,
    comentario text,
    detalles jsonb DEFAULT '{}'::jsonb NOT NULL,
    ts timestamp with time zone DEFAULT now() NOT NULL,
    to_dept_code integer,
    from_dept_code integer,
    CONSTRAINT flow_events_pkey PRIMARY KEY (id)
);

CREATE INDEX ix_flow_events_radicado ON flow_events USING btree (radicado_id, ts);
CREATE INDEX ix_flow_events_to_dept_code ON flow_events USING btree (to_dept_code);

-- ── visto_bueno ───────────────────────────────────────────────────────────────

CREATE TABLE visto_bueno (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    radicado_id uuid NOT NULL,
    orden integer NOT NULL,
    revisor_id uuid NOT NULL,
    estado character varying(12) DEFAULT 'pendiente'::character varying NOT NULL,
    comentario text,
    decidido_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT visto_bueno_estado_check CHECK ((estado IN ('pendiente', 'aprobado', 'rechazado'))),
    CONSTRAINT visto_bueno_pkey PRIMARY KEY (id),
    CONSTRAINT visto_bueno_radicado_id_orden_key UNIQUE (radicado_id, orden)
);

CREATE INDEX ix_vobo_radicado ON visto_bueno USING btree (radicado_id, orden);

-- ── flow_step_saneamiento ─────────────────────────────────────────────────────

CREATE TABLE flow_step_saneamiento (
    id bigint NOT NULL,
    step_id uuid NOT NULL,
    radicado_id uuid NOT NULL,
    tracking_number character varying(50) NOT NULL,
    estado_radicado character varying(20) NOT NULL,
    paso_antes character varying(20) NOT NULL,
    paso_despues character varying(20) NOT NULL,
    flow_event_id uuid NOT NULL,
    origen character varying(40) DEFAULT 'migracion_015'::character varying NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT flow_step_saneamiento_pkey PRIMARY KEY (id)
);

ALTER TABLE flow_step_saneamiento ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME flow_step_saneamiento_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

-- ── Functions ─────────────────────────────────────────────────────────────────

CREATE FUNCTION reject_flow_events_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'flow_events is append-only (Ley 594/2000)';
END;
$$;

CREATE FUNCTION reject_flow_step_saneamiento_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'flow_step_saneamiento is append-only (evidencia de la migración 015)';
END;
$$;

-- ── Triggers ──────────────────────────────────────────────────────────────────

CREATE TRIGGER trg_flow_events_immutable BEFORE DELETE OR UPDATE ON flow_events FOR EACH ROW EXECUTE FUNCTION reject_flow_events_mutation();

CREATE TRIGGER trg_flow_step_saneamiento_immutable BEFORE DELETE OR UPDATE ON flow_step_saneamiento FOR EACH ROW EXECUTE FUNCTION reject_flow_step_saneamiento_mutation();
