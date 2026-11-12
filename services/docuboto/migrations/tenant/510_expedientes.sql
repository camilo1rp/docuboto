-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Case files, their records, events, metadata, holds and tombstones.

-- ── expediente_sequences ──────────────────────────────────────────────────────

CREATE TABLE expediente_sequences (
    year integer NOT NULL,
    last_seq integer DEFAULT 0 NOT NULL,
    CONSTRAINT expediente_sequences_pkey PRIMARY KEY (year)
);

-- ── expedientes ───────────────────────────────────────────────────────────────

CREATE TABLE expedientes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    code character varying(50) NOT NULL,
    name text NOT NULL,
    description text,
    trd_serie_id uuid,
    status character varying(20) DEFAULT 'open'::character varying NOT NULL,
    opened_by uuid,
    opened_at timestamp with time zone DEFAULT now() NOT NULL,
    closed_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    metadata_template_id uuid,
    disposition jsonb,
    nivel_seguridad smallint DEFAULT 1 NOT NULL,
    trd_serie_code character varying(20),
    trd_revision_requerida boolean DEFAULT false NOT NULL,
    trd_revision_motivo text,
    origen character varying(20) DEFAULT 'corriente'::character varying NOT NULL,
    fecha_extrema_inicial date,
    fecha_extrema_final date,
    publicable boolean DEFAULT false NOT NULL,
    publicado_at timestamp with time zone,
    publicado_por uuid,
    search_vector tsvector GENERATED ALWAYS AS (((setweight(to_tsvector('public.spanish_unaccent'::regconfig, (COALESCE(code, ''::character varying))::text), 'A'::"char") || setweight(to_tsvector('public.spanish_unaccent'::regconfig, COALESCE(name, ''::text)), 'B'::"char")) || setweight(to_tsvector('public.spanish_unaccent'::regconfig, COALESCE(description, ''::text)), 'C'::"char"))) STORED,
    CONSTRAINT ck_expedientes_nivel_seguridad CHECK (((nivel_seguridad >= 1) AND (nivel_seguridad <= 3))),
    CONSTRAINT expedientes_corriente_forma_check CHECK ((((origen)::text <> 'corriente'::text) OR ((fecha_extrema_inicial IS NULL) AND (fecha_extrema_final IS NULL)))),
    CONSTRAINT expedientes_fondo_acumulado_forma_check CHECK ((((origen)::text <> 'fondo_acumulado'::text) OR ((fecha_extrema_final IS NOT NULL) AND ((fecha_extrema_inicial IS NULL) OR (fecha_extrema_inicial <= fecha_extrema_final))))),
    CONSTRAINT expedientes_origen_check CHECK ((origen IN ('corriente', 'fondo_acumulado'))),
    CONSTRAINT expedientes_code_key UNIQUE (code),
    CONSTRAINT expedientes_pkey PRIMARY KEY (id),
    CONSTRAINT expedientes_trd_serie_id_fkey FOREIGN KEY (trd_serie_id) REFERENCES trd_series(id)
);

COMMENT ON COLUMN expedientes.publicable IS 'Esquema de publicación (Ley 1712/2014 art. 12): acto EXPLÍCITO por el que la entidad difunde el expediente de oficio (OAI-PMH EAD multinivel). FALSE por defecto — la ausencia de reserva no equivale a una decisión de publicar. Condición NECESARIA pero no suficiente: el recorte exige además nivel_seguridad = 1.';

CREATE INDEX ix_expedientes_code ON expedientes USING btree (code);
CREATE INDEX ix_expedientes_metadata_gin ON expedientes USING gin (metadata);
CREATE INDEX ix_expedientes_nivel_seguridad ON expedientes USING btree (nivel_seguridad);
CREATE INDEX ix_expedientes_origen ON expedientes USING btree (origen);
CREATE INDEX ix_expedientes_publicables ON expedientes USING btree (opened_at) WHERE (publicable AND (nivel_seguridad = 1));
CREATE INDEX ix_expedientes_search ON expedientes USING gin (search_vector);
CREATE INDEX ix_expedientes_status ON expedientes USING btree (status);
CREATE INDEX ix_expedientes_trd_revision ON expedientes USING btree (trd_revision_requerida) WHERE trd_revision_requerida;
CREATE INDEX ix_expedientes_trd_serie_code ON expedientes USING btree (trd_serie_code);

-- ── expediente_radicados ──────────────────────────────────────────────────────

CREATE TABLE expediente_radicados (
    expediente_id uuid NOT NULL,
    radicado_id uuid NOT NULL,
    tracking_number character varying(50) NOT NULL,
    added_at timestamp with time zone DEFAULT now() NOT NULL,
    content_hash character(64),
    formato text,
    tamano_bytes bigint,
    folios integer,
    orden_documental integer,
    estado text DEFAULT 'vinculado'::text NOT NULL,
    excluido_at timestamp with time zone,
    excluido_causal text,
    dependencia_productora_codigo integer,
    dependencia_productora_nombre text,
    tipologia text,
    origen_completado_at timestamp with time zone,
    CONSTRAINT ck_expediente_radicados_estado CHECK ((estado = ANY (ARRAY['vinculado'::text, 'excluido'::text]))),
    CONSTRAINT expediente_radicados_dependencia_productora_pair_check CHECK (((dependencia_productora_codigo IS NULL) = (dependencia_productora_nombre IS NULL))),
    CONSTRAINT expediente_radicados_folios_check CHECK (((folios IS NULL) OR (folios >= 0))),
    CONSTRAINT expediente_radicados_tamano_bytes_check CHECK (((tamano_bytes IS NULL) OR (tamano_bytes >= 0))),
    CONSTRAINT expediente_radicados_pkey PRIMARY KEY (expediente_id, radicado_id),
    CONSTRAINT expediente_radicados_expediente_id_fkey FOREIGN KEY (expediente_id) REFERENCES expedientes(id) ON DELETE CASCADE
);

COMMENT ON COLUMN expediente_radicados.formato IS 'Tipo MIME del documento aportado al vincular (E15 RT-15, Opción A); NULL = no declarado.';

COMMENT ON COLUMN expediente_radicados.tamano_bytes IS 'Tamaño en bytes del documento aportado al vincular (E15 RT-15, Opción A); NULL = no declarado.';

COMMENT ON COLUMN expediente_radicados.folios IS 'Número de folios del documento aportado al vincular (E15 RT-15, Opción A); NULL = no declarado.';

COMMENT ON COLUMN expediente_radicados.orden_documental IS 'Orden documental ESTABLE (RT-15 art. 4.3.2.3): asignado UNA sola vez al vincular (MAX+1 por expediente, atómico), inmutable — nunca se renumera ni se reutiliza tras una exclusión (soft-delete). NULL solo de forma transitoria antes del backfill.';

COMMENT ON COLUMN expediente_radicados.estado IS 'vinculado = vigente en el expediente; excluido = soft-delete (unlink): la fila permanece para que el índice electrónico muestre el hueco trazable (Acuerdo AGN 001/2024 art. 4.3.2.3). Paridad con el Orfeo legado (estado=2, sin DELETE físico).';

COMMENT ON COLUMN expediente_radicados.excluido_at IS 'Marca de tiempo de la exclusión (soft-delete); NULL mientras estado=vinculado.';

COMMENT ON COLUMN expediente_radicados.excluido_causal IS 'Causal opcional de la exclusión, aportado por quien excluye (unlink).';

COMMENT ON COLUMN expediente_radicados.dependencia_productora_codigo IS 'Código de la dependencia productora del documento (E15 perfil v4, R1), aportado por quien vincula (Opción A, igual que formato/tamano_bytes/folios). NULL = no declarado (fila legacy); siempre acompañado de _nombre (CHECK de pareja).';

COMMENT ON COLUMN expediente_radicados.dependencia_productora_nombre IS 'Nombre de la dependencia productora del documento (E15 perfil v4, R1); ver dependencia_productora_codigo.';

COMMENT ON COLUMN expediente_radicados.tipologia IS 'Tipología documental (doc_class del radicado) aportada al vincular (Opción A, E15 perfil v4 / RT-15 art. 4.3.2.3). Se materializa como <tipologia> del índice electrónico. NULL = no declarado.';

COMMENT ON COLUMN expediente_radicados.origen_completado_at IS 'Instante en que el job de backfill intentó completar productora/formato/tamano_bytes de este vínculo con lo que sabe document-service (AREA-07-01). NULL = aún no intentado. Un vínculo ya completado (o creado con el origen resuelto en el servidor) no se vuelve a elegir.';

CREATE INDEX ix_exp_rad_orden ON expediente_radicados USING btree (expediente_id, orden_documental);
CREATE INDEX ix_exp_rad_origen_pendiente ON expediente_radicados USING btree (expediente_id) WHERE ((origen_completado_at IS NULL) AND (estado = 'vinculado'::text));
CREATE INDEX ix_exp_rad_radicado_id ON expediente_radicados USING btree (radicado_id);
CREATE UNIQUE INDEX ux_exp_rad_orden ON expediente_radicados USING btree (expediente_id, orden_documental) WHERE (orden_documental IS NOT NULL);

-- ── expediente_eventos ────────────────────────────────────────────────────────

CREATE TABLE expediente_eventos (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    expediente_id uuid NOT NULL,
    numero_expediente text,
    accion text NOT NULL,
    actor uuid,
    comentario text,
    detalles jsonb DEFAULT '{}'::jsonb NOT NULL,
    ts timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT expediente_eventos_pkey PRIMARY KEY (id)
);

CREATE INDEX ix_expediente_eventos_exp ON expediente_eventos USING btree (expediente_id, ts);

-- ── expediente_metadata_templates ─────────────────────────────────────────────

CREATE TABLE expediente_metadata_templates (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    trd_serie_id uuid NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    json_schema jsonb NOT NULL,
    activo boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT expediente_metadata_templates_pkey PRIMARY KEY (id),
    CONSTRAINT expediente_metadata_templates_trd_serie_id_version_key UNIQUE (trd_serie_id, version)
);

CREATE UNIQUE INDEX ux_exp_metadata_templates_activa ON expediente_metadata_templates USING btree (trd_serie_id) WHERE activo;

-- ── expediente_valoracion_hold ────────────────────────────────────────────────

CREATE TABLE expediente_valoracion_hold (
    expediente_id uuid NOT NULL,
    hold_id uuid NOT NULL,
    trd_serie_id uuid NOT NULL,
    fijado_at timestamp with time zone DEFAULT now() NOT NULL,
    valoracion_id uuid,
    aplicada_at timestamp with time zone,
    resultado character varying(16),
    intentos integer DEFAULT 0 NOT NULL,
    next_attempt_at timestamp with time zone,
    last_error text,
    agotada boolean DEFAULT false NOT NULL,
    seleccion character varying(10),
    seleccion_por uuid,
    seleccion_at timestamp with time zone,
    seleccion_fundamento text,
    CONSTRAINT ck_valoracion_hold_agotada_sin_aplicar CHECK (((NOT agotada) OR (aplicada_at IS NULL))),
    CONSTRAINT ck_valoracion_hold_aplicada_coherente CHECK ((((aplicada_at IS NULL) = (resultado IS NULL)) AND ((aplicada_at IS NULL) OR (valoracion_id IS NOT NULL)))),
    CONSTRAINT ck_valoracion_hold_resultado CHECK (((resultado IS NULL) OR (resultado IN ('retencion_ct', 'sin_retencion')))),
    CONSTRAINT ck_valoracion_hold_seleccion CHECK (((seleccion IS NULL) OR ((seleccion IN ('conservar', 'descartar')) AND (seleccion_por IS NOT NULL) AND (seleccion_at IS NOT NULL) AND (length(btrim(COALESCE(seleccion_fundamento, ''::text))) >= 10) AND (valoracion_id IS NOT NULL)))),
    CONSTRAINT expediente_valoracion_hold_pkey PRIMARY KEY (expediente_id),
    CONSTRAINT expediente_valoracion_hold_expediente_id_fkey FOREIGN KEY (expediente_id) REFERENCES expedientes(id) ON DELETE CASCADE,
    CONSTRAINT expediente_valoracion_hold_trd_serie_id_fkey FOREIGN KEY (trd_serie_id) REFERENCES trd_series(id),
    CONSTRAINT expediente_valoracion_hold_valoracion_id_fkey FOREIGN KEY (valoracion_id) REFERENCES tvd_valoracion(id)
);

COMMENT ON TABLE expediente_valoracion_hold IS 'Cola de la valoración (ADR-043): expediente de fondo acumulado con calendario vencido, su hold `valoracion_pendiente` en storage y el estado de aplicación. pendiente (valoracion_id NULL) -> valorada -> aplicada | agotada.';

CREATE INDEX ix_valoracion_hold_pendientes ON expediente_valoracion_hold USING btree (next_attempt_at NULLS FIRST) WHERE ((valoracion_id IS NOT NULL) AND (aplicada_at IS NULL) AND (NOT agotada));
CREATE INDEX ix_valoracion_hold_serie ON expediente_valoracion_hold USING btree (trd_serie_id);

-- ── expediente_lapida ─────────────────────────────────────────────────────────

CREATE TABLE expediente_lapida (
    expediente_id uuid NOT NULL,
    expediente_code text NOT NULL,
    trd_serie_code text,
    fecha_extrema_inicial date,
    fecha_extrema_final date,
    radicados_eliminados integer DEFAULT 0 NOT NULL,
    anexos_eliminados integer DEFAULT 0 NOT NULL,
    lote_id uuid NOT NULL,
    lote_codigo text NOT NULL,
    acta_comite text NOT NULL,
    acto_administrativo text NOT NULL,
    acta_sha256 text NOT NULL,
    eliminado_por uuid NOT NULL,
    eliminado_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT expediente_lapida_sha_forma_check CHECK ((acta_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT expediente_lapida_pkey PRIMARY KEY (expediente_id)
);

COMMENT ON TABLE expediente_lapida IS 'Lápida de un expediente eliminado por disposición final (E04, RF-TRD-07 fase 5) -- CONTENT-FREE y permanente: dice QUE existió y bajo qué acto se destruyó, nunca qué contenía.';

CREATE INDEX ix_expediente_lapida_code ON expediente_lapida USING btree (expediente_code);
CREATE INDEX ix_expediente_lapida_lote ON expediente_lapida USING btree (lote_id);

-- ── Functions ─────────────────────────────────────────────────────────────────

CREATE FUNCTION assert_expediente_pin_tipo_instrumento() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    pin_tipo VARCHAR(3);
BEGIN
    IF NEW.trd_serie_id IS NOT NULL
       AND (TG_OP = 'INSERT' OR NEW.trd_serie_id IS DISTINCT FROM OLD.trd_serie_id) THEN
        SELECT tipo_instrumento INTO pin_tipo FROM trd_series WHERE id = NEW.trd_serie_id;

        IF NEW.origen = 'corriente' AND pin_tipo = 'TVD' THEN
            RAISE EXCEPTION 'expedientes: un expediente origen=corriente no puede amarrarse a la TVD % -- el fondo acumulado se registra con origen=fondo_acumulado (ADR-026 D3)',
                NEW.trd_serie_id;
        ELSIF NEW.origen = 'fondo_acumulado' AND pin_tipo = 'TRD' THEN
            RAISE EXCEPTION 'expedientes: un expediente origen=fondo_acumulado no puede amarrarse a la TRD % -- el fondo acumulado se valora con una TVD (ADR-026 D3)',
                NEW.trd_serie_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION expediente_guard_unidad_prestada() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    cod text;
BEGIN
    cod := expediente_unidad_prestada(OLD.id);
    IF cod IS NOT NULL THEN
        RAISE EXCEPTION 'unidad_prestada: %', cod
            USING ERRCODE = '23514', CONSTRAINT = 'ck_expediente_unidad_prestada';
    END IF;
    RETURN OLD;
END;
$$;

CREATE FUNCTION guard_expediente_valoracion_hold() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    disp VARCHAR(2);
BEGIN
    IF NEW.hold_id IS DISTINCT FROM OLD.hold_id
       OR NEW.trd_serie_id IS DISTINCT FROM OLD.trd_serie_id
       OR NEW.expediente_id IS DISTINCT FROM OLD.expediente_id THEN
        RAISE EXCEPTION 'expediente_valoracion_hold: hold_id, trd_serie_id y expediente_id son inmutables';
    END IF;
    IF OLD.valoracion_id IS NOT NULL AND NEW.valoracion_id IS DISTINCT FROM OLD.valoracion_id THEN
        RAISE EXCEPTION 'expediente_valoracion_hold: valoracion_id ya registrada es inmutable';
    END IF;
    IF OLD.aplicada_at IS NOT NULL AND (NEW.aplicada_at IS DISTINCT FROM OLD.aplicada_at
                                        OR NEW.resultado IS DISTINCT FROM OLD.resultado) THEN
        RAISE EXCEPTION 'expediente_valoracion_hold: una valoración aplicada es inmutable';
    END IF;
    -- La selección se decide UNA vez (fill-once) y no se reescribe.
    IF OLD.seleccion IS NOT NULL AND (NEW.seleccion IS DISTINCT FROM OLD.seleccion
                                      OR NEW.seleccion_por IS DISTINCT FROM OLD.seleccion_por
                                      OR NEW.seleccion_at IS DISTINCT FROM OLD.seleccion_at
                                      OR NEW.seleccion_fundamento IS DISTINCT FROM OLD.seleccion_fundamento) THEN
        RAISE EXCEPTION 'expediente_valoracion_hold: la selección ya registrada es inmutable';
    END IF;
    -- Una valoración de SELECCIÓN no se aplica sin el acto de selección del expediente.
    IF NEW.aplicada_at IS NOT NULL AND OLD.aplicada_at IS NULL AND NEW.seleccion IS NULL THEN
        SELECT disposicion INTO disp FROM tvd_valoracion WHERE id = NEW.valoracion_id;
        IF disp = 'S' THEN
            RAISE EXCEPTION 'expediente_valoracion_hold: una valoración S exige la selección del expediente';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION reject_expediente_eventos_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'expediente_eventos is append-only (Ley 594/2000)';
END;
$$;

CREATE FUNCTION reject_expediente_lapida_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION
        'expediente_lapida es permanente y no admite % (E04, RF-TRD-07)', TG_OP;
END;
$$;

CREATE FUNCTION reject_trd_pin_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF OLD.status IN ('closed','transferred')
       AND NEW.trd_serie_id IS DISTINCT FROM OLD.trd_serie_id THEN
        RAISE EXCEPTION 'expedientes: la version TRD que gobierna un expediente % es inmutable tras el cierre (ADR-025)', OLD.status;
    END IF;
    RETURN NEW;
END;
$$;

-- ── Triggers ──────────────────────────────────────────────────────────────────

CREATE TRIGGER trg_expediente_unidad_prestada BEFORE DELETE ON expedientes FOR EACH ROW EXECUTE FUNCTION expediente_guard_unidad_prestada();
CREATE TRIGGER trg_expedientes_pin_tipo BEFORE INSERT OR UPDATE ON expedientes FOR EACH ROW EXECUTE FUNCTION assert_expediente_pin_tipo_instrumento();
CREATE TRIGGER trg_expedientes_trd_pin_immutable BEFORE UPDATE ON expedientes FOR EACH ROW EXECUTE FUNCTION reject_trd_pin_mutation();

CREATE TRIGGER trg_expediente_eventos_immutable BEFORE DELETE OR UPDATE ON expediente_eventos FOR EACH ROW EXECUTE FUNCTION reject_expediente_eventos_mutation();

CREATE TRIGGER trg_expediente_valoracion_hold_guard BEFORE UPDATE ON expediente_valoracion_hold FOR EACH ROW EXECUTE FUNCTION guard_expediente_valoracion_hold();

CREATE TRIGGER trg_expediente_lapida_append_only BEFORE DELETE OR UPDATE ON expediente_lapida FOR EACH ROW EXECUTE FUNCTION reject_expediente_lapida_mutation();
