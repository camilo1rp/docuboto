-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- PINAR: the institutional archives plan.

-- ── pinar_eje ─────────────────────────────────────────────────────────────────

CREATE TABLE pinar_eje (
    code smallint NOT NULL,
    nombre character varying(120) NOT NULL,
    CONSTRAINT pinar_eje_pkey PRIMARY KEY (code)
);

INSERT INTO pinar_eje (code, nombre) VALUES
    ('1', 'Administración de archivos'),
    ('2', 'Acceso a la información'),
    ('3', 'Preservación de la información'),
    ('4', 'Aspectos tecnológicos y de seguridad'),
    ('5', 'Fortalecimiento y articulación');

-- ── pinar_plan ────────────────────────────────────────────────────────────────

CREATE TABLE pinar_plan (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    vigencia_desde date,
    vigencia_hasta date,
    vision text,
    acto_administrativo character varying(200),
    estado character varying(20) DEFAULT 'borrador'::character varying NOT NULL,
    aprobado_por uuid,
    aprobado_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT pinar_plan_check CHECK (((vigencia_hasta IS NULL) OR (vigencia_desde IS NULL) OR (vigencia_hasta >= vigencia_desde))),
    CONSTRAINT pinar_plan_estado_check CHECK ((estado IN ('borrador', 'aprobado', 'en_ejecucion', 'cerrado'))),
    CONSTRAINT pinar_plan_pkey PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ux_pinar_plan_en_ejecucion ON pinar_plan USING btree (estado) WHERE ((estado)::text = 'en_ejecucion'::text);

-- ── pinar_aspecto_critico ─────────────────────────────────────────────────────

CREATE TABLE pinar_aspecto_critico (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    plan_id uuid NOT NULL,
    descripcion text NOT NULL,
    riesgo text,
    orden smallint DEFAULT 0 NOT NULL,
    CONSTRAINT pinar_aspecto_critico_pkey PRIMARY KEY (id),
    CONSTRAINT pinar_aspecto_critico_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES pinar_plan(id) ON DELETE CASCADE
);

CREATE INDEX ix_pinar_aspecto_plan ON pinar_aspecto_critico USING btree (plan_id, orden);

-- ── pinar_priorizacion ────────────────────────────────────────────────────────

CREATE TABLE pinar_priorizacion (
    aspecto_id uuid NOT NULL,
    eje_code smallint NOT NULL,
    impacto smallint NOT NULL,
    CONSTRAINT pinar_priorizacion_impacto_check CHECK (((impacto >= 1) AND (impacto <= 10))),
    CONSTRAINT pinar_priorizacion_pkey PRIMARY KEY (aspecto_id, eje_code),
    CONSTRAINT pinar_priorizacion_aspecto_id_fkey FOREIGN KEY (aspecto_id) REFERENCES pinar_aspecto_critico(id) ON DELETE CASCADE,
    CONSTRAINT pinar_priorizacion_eje_code_fkey FOREIGN KEY (eje_code) REFERENCES pinar_eje(code)
);

-- ── pinar_objetivo ────────────────────────────────────────────────────────────

CREATE TABLE pinar_objetivo (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    plan_id uuid NOT NULL,
    descripcion text NOT NULL,
    orden smallint DEFAULT 0 NOT NULL,
    CONSTRAINT pinar_objetivo_pkey PRIMARY KEY (id),
    CONSTRAINT pinar_objetivo_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES pinar_plan(id) ON DELETE CASCADE
);

CREATE INDEX ix_pinar_objetivo_plan ON pinar_objetivo USING btree (plan_id, orden);

-- ── pinar_proyecto ────────────────────────────────────────────────────────────

CREATE TABLE pinar_proyecto (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    plan_id uuid NOT NULL,
    objetivo_id uuid,
    nombre character varying(200) NOT NULL,
    meta text,
    indicador text,
    responsable character varying(200),
    recurso text,
    fecha_inicio date,
    fecha_fin date,
    estado character varying(20) DEFAULT 'planeado'::character varying NOT NULL,
    avance_pct smallint DEFAULT 0 NOT NULL,
    CONSTRAINT pinar_proyecto_avance_pct_check CHECK (((avance_pct >= 0) AND (avance_pct <= 100))),
    CONSTRAINT pinar_proyecto_check CHECK (((fecha_fin IS NULL) OR (fecha_inicio IS NULL) OR (fecha_fin >= fecha_inicio))),
    CONSTRAINT pinar_proyecto_estado_check CHECK ((estado IN ('planeado', 'en_curso', 'terminado', 'cancelado'))),
    CONSTRAINT pinar_proyecto_pkey PRIMARY KEY (id),
    CONSTRAINT pinar_proyecto_objetivo_id_fkey FOREIGN KEY (objetivo_id) REFERENCES pinar_objetivo(id) ON DELETE SET NULL,
    CONSTRAINT pinar_proyecto_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES pinar_plan(id) ON DELETE CASCADE
);

CREATE INDEX ix_pinar_proyecto_objetivo ON pinar_proyecto USING btree (objetivo_id);
CREATE INDEX ix_pinar_proyecto_plan ON pinar_proyecto USING btree (plan_id);

-- ── pinar_proyecto_aspecto ────────────────────────────────────────────────────

CREATE TABLE pinar_proyecto_aspecto (
    proyecto_id uuid NOT NULL,
    aspecto_id uuid NOT NULL,
    CONSTRAINT pinar_proyecto_aspecto_pkey PRIMARY KEY (proyecto_id, aspecto_id),
    CONSTRAINT pinar_proyecto_aspecto_aspecto_id_fkey FOREIGN KEY (aspecto_id) REFERENCES pinar_aspecto_critico(id) ON DELETE CASCADE,
    CONSTRAINT pinar_proyecto_aspecto_proyecto_id_fkey FOREIGN KEY (proyecto_id) REFERENCES pinar_proyecto(id) ON DELETE CASCADE
);

-- ── pinar_seguimiento ─────────────────────────────────────────────────────────

CREATE TABLE pinar_seguimiento (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    proyecto_id uuid NOT NULL,
    fecha date NOT NULL,
    avance_pct smallint NOT NULL,
    observacion text,
    registrado_por uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT pinar_seguimiento_avance_pct_check CHECK (((avance_pct >= 0) AND (avance_pct <= 100))),
    CONSTRAINT pinar_seguimiento_pkey PRIMARY KEY (id),
    CONSTRAINT pinar_seguimiento_proyecto_id_fkey FOREIGN KEY (proyecto_id) REFERENCES pinar_proyecto(id) ON DELETE CASCADE
);

CREATE INDEX ix_pinar_seguimiento_proyecto ON pinar_seguimiento USING btree (proyecto_id, created_at);

-- ── pinar_instrumento_ref ─────────────────────────────────────────────────────

CREATE TABLE pinar_instrumento_ref (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    plan_id uuid NOT NULL,
    tipo character varying(30) NOT NULL,
    ref character varying(200) NOT NULL,
    descripcion text,
    CONSTRAINT pinar_instrumento_ref_tipo_check CHECK ((tipo IN ('ccd', 'trd', 'fuid', 'plan_preservacion', 'plan_contingencia'))),
    CONSTRAINT pinar_instrumento_ref_pkey PRIMARY KEY (id),
    CONSTRAINT pinar_instrumento_ref_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES pinar_plan(id) ON DELETE CASCADE
);

CREATE INDEX ix_pinar_instrumento_plan ON pinar_instrumento_ref USING btree (plan_id);

-- ── Functions ─────────────────────────────────────────────────────────────────

CREATE FUNCTION pinar_seguimiento_append_only() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'pinar_seguimiento is append-only (no % permitido)', TG_OP;
END;
$$;

-- ── Triggers ──────────────────────────────────────────────────────────────────

CREATE TRIGGER trg_pinar_seguimiento_no_truncate BEFORE TRUNCATE ON pinar_seguimiento FOR EACH STATEMENT EXECUTE FUNCTION pinar_seguimiento_append_only();
CREATE TRIGGER trg_pinar_seguimiento_no_update BEFORE DELETE OR UPDATE ON pinar_seguimiento FOR EACH ROW EXECUTE FUNCTION pinar_seguimiento_append_only();
