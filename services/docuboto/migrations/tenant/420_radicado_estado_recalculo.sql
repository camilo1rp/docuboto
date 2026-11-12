-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Append-only logs of state, location and unit-code recalculations.

-- ── radicado_estado_recalculo ─────────────────────────────────────────────────

CREATE TABLE radicado_estado_recalculo (
    id bigint NOT NULL,
    radicado_id uuid NOT NULL,
    tracking_number character varying(50) NOT NULL,
    antes character varying(20) NOT NULL,
    despues character varying(20) NOT NULL,
    paso_activo character varying(20),
    origen character varying(40) DEFAULT 'migracion_013'::character varying NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT radicado_estado_recalculo_pkey PRIMARY KEY (id)
);

ALTER TABLE radicado_estado_recalculo ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME radicado_estado_recalculo_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

-- ── radicado_ubicacion_recalculo ──────────────────────────────────────────────

CREATE TABLE radicado_ubicacion_recalculo (
    id bigint NOT NULL,
    radicado_id uuid NOT NULL,
    tracking_number character varying(50) NOT NULL,
    estado_radicado character varying(20) NOT NULL,
    antes_dept character varying(100),
    antes_dept_code integer,
    antes_usuario uuid,
    despues_dept character varying(100),
    despues_dept_code integer,
    despues_usuario uuid,
    paso_id uuid,
    origen character varying(40) DEFAULT 'migracion_016'::character varying NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT radicado_ubicacion_recalculo_pkey PRIMARY KEY (id)
);

ALTER TABLE radicado_ubicacion_recalculo ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME radicado_ubicacion_recalculo_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

-- ── dependencia_codigo_backfill ───────────────────────────────────────────────

CREATE TABLE dependencia_codigo_backfill (
    id bigint NOT NULL,
    tabla character varying(40) NOT NULL,
    fila_id uuid NOT NULL,
    columna character varying(60) NOT NULL,
    nombre text NOT NULL,
    codigo integer,
    candidatos integer NOT NULL,
    resultado character varying(10) NOT NULL,
    vivo boolean NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    antes jsonb,
    despues jsonb,
    historico_id bigint,
    reconstruido boolean DEFAULT false NOT NULL,
    CONSTRAINT dependencia_codigo_backfill_resultado_check CHECK ((resultado IN ('resuelto', 'mapeado', 'ambiguo', 'huerfano'))),
    CONSTRAINT dependencia_codigo_backfill_pkey PRIMARY KEY (id)
);

ALTER TABLE dependencia_codigo_backfill ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME dependencia_codigo_backfill_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE INDEX ix_dependencia_codigo_backfill_fila ON dependencia_codigo_backfill USING btree (tabla, fila_id, columna);

-- ── Functions ─────────────────────────────────────────────────────────────────

CREATE FUNCTION reject_dependencia_codigo_backfill_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'dependencia_codigo_backfill is append-only (evidencia de la migración 019)';
END;
$$;

CREATE FUNCTION reject_radicado_estado_recalculo_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'radicado_estado_recalculo is append-only (evidencia de la migración 013)';
END;
$$;

CREATE FUNCTION reject_radicado_ubicacion_recalculo_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'radicado_ubicacion_recalculo is append-only (evidencia de la migración 016)';
END;
$$;

-- ── Triggers ──────────────────────────────────────────────────────────────────

CREATE TRIGGER trg_radicado_estado_recalculo_immutable BEFORE DELETE OR UPDATE ON radicado_estado_recalculo FOR EACH ROW EXECUTE FUNCTION reject_radicado_estado_recalculo_mutation();

CREATE TRIGGER trg_radicado_ubicacion_recalculo_immutable BEFORE DELETE OR UPDATE ON radicado_ubicacion_recalculo FOR EACH ROW EXECUTE FUNCTION reject_radicado_ubicacion_recalculo_mutation();

CREATE TRIGGER trg_dependencia_codigo_backfill_immutable BEFORE DELETE OR UPDATE ON dependencia_codigo_backfill FOR EACH ROW EXECUTE FUNCTION reject_dependencia_codigo_backfill_mutation();
