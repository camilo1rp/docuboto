-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Retention schedules (TRD/TVD), their producers and document types.

-- ── trd_series ────────────────────────────────────────────────────────────────

CREATE TABLE trd_series (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    code character varying(20) NOT NULL,
    name text NOT NULL,
    description text,
    disposition character varying(20) DEFAULT 'eliminate'::character varying NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    parent_id uuid,
    archivo_gestion_years integer,
    archivo_central_years integer,
    version integer DEFAULT 1 NOT NULL,
    valid_from date DEFAULT CURRENT_DATE NOT NULL,
    pdfa_profile text,
    estado character varying(20) DEFAULT 'borrador'::character varying NOT NULL,
    valid_to date,
    acto_administrativo text,
    fecha_aprobacion_comite date,
    fecha_convalidacion date,
    instancia_convalidante text,
    rusd_radicado text,
    fecha_publicacion date,
    supersede_a uuid,
    created_by uuid,
    acta_comite text,
    tipo_instrumento character varying(3) DEFAULT 'TRD'::character varying NOT NULL,
    fondo_nombre text,
    fecha_extrema_inicial date,
    fecha_extrema_final date,
    justificacion_valoracion text,
    CONSTRAINT trd_series_convalidada_acto_check CHECK ((((estado)::text <> 'convalidada'::text) OR ((acto_administrativo IS NOT NULL) AND (fecha_convalidacion IS NOT NULL) AND (instancia_convalidante IS NOT NULL)))),
    CONSTRAINT trd_series_estado_check CHECK ((estado IN ('borrador', 'aprobada', 'convalidada', 'migrada', 'derogada'))),
    CONSTRAINT trd_series_fases_check CHECK (((((tipo_instrumento)::text = 'TRD'::text) AND (archivo_gestion_years IS NOT NULL) AND (archivo_central_years IS NOT NULL)) OR (((tipo_instrumento)::text = 'TVD'::text) AND (archivo_central_years IS NOT NULL)))),
    CONSTRAINT trd_series_migrada_sin_acto_check CHECK ((((estado)::text <> 'migrada'::text) OR ((acto_administrativo IS NULL) AND (fecha_convalidacion IS NULL) AND (instancia_convalidante IS NULL) AND (rusd_radicado IS NULL) AND (fecha_aprobacion_comite IS NULL)))),
    CONSTRAINT trd_series_pdfa_profile_check CHECK (((pdfa_profile IS NULL) OR (pdfa_profile = ANY (ARRAY['1a'::text, '1b'::text, '2a'::text, '2b'::text, '2u'::text, '3a'::text, '3b'::text, '3u'::text])))),
    CONSTRAINT trd_series_retencion_minima_check CHECK ((((tipo_instrumento)::text <> 'TRD'::text) OR ((archivo_gestion_years + archivo_central_years) >= 1))),
    CONSTRAINT trd_series_tipo_instrumento_check CHECK ((tipo_instrumento IN ('TRD', 'TVD'))),
    CONSTRAINT trd_series_trd_forma_check CHECK ((((tipo_instrumento)::text <> 'TRD'::text) OR ((fondo_nombre IS NULL) AND (justificacion_valoracion IS NULL) AND (fecha_extrema_inicial IS NULL) AND (fecha_extrema_final IS NULL)))),
    CONSTRAINT trd_series_tvd_forma_check CHECK ((((tipo_instrumento)::text <> 'TVD'::text) OR ((fondo_nombre IS NOT NULL) AND (justificacion_valoracion IS NOT NULL) AND (fecha_extrema_inicial IS NOT NULL) AND (fecha_extrema_final IS NOT NULL) AND (fecha_extrema_final >= fecha_extrema_inicial) AND (COALESCE(archivo_gestion_years, 0) = 0)))),
    CONSTRAINT trd_series_vigencia_check CHECK ((((valid_to IS NULL) OR (valid_to >= valid_from)) AND (((estado)::text <> 'derogada'::text) OR (valid_to IS NOT NULL)) AND (version >= 1))),
    CONSTRAINT trd_series_code_version_key UNIQUE (code, version),
    CONSTRAINT trd_series_pkey PRIMARY KEY (id),
    CONSTRAINT trd_series_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES trd_series(id) ON DELETE SET NULL,
    CONSTRAINT trd_series_supersede_a_fkey FOREIGN KEY (supersede_a) REFERENCES trd_series(id)
);

CREATE INDEX ix_trd_series_code ON trd_series USING btree (code);
CREATE INDEX ix_trd_series_code_version ON trd_series USING btree (code, version DESC);
CREATE INDEX ix_trd_series_parent ON trd_series USING btree (parent_id);
CREATE INDEX ix_trd_series_tipo_code ON trd_series USING btree (tipo_instrumento, code);
CREATE UNIQUE INDEX ux_trd_series_en_tramite ON trd_series USING btree (code) WHERE (estado IN ('borrador', 'aprobada'));
CREATE UNIQUE INDEX ux_trd_series_vigente ON trd_series USING btree (code) WHERE ((estado IN ('convalidada', 'migrada')) AND (valid_to IS NULL));

-- ── trd_serie_dependencia_productora ──────────────────────────────────────────

CREATE TABLE trd_serie_dependencia_productora (
    trd_serie_id uuid NOT NULL,
    dependencia_codigo integer NOT NULL,
    dependencia_nombre character varying(100) NOT NULL,
    ruta_convalidada jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trd_serie_dependencia_productora_pkey PRIMARY KEY (trd_serie_id, dependencia_codigo),
    CONSTRAINT trd_serie_dependencia_productora_trd_serie_id_fkey FOREIGN KEY (trd_serie_id) REFERENCES trd_series(id) ON DELETE CASCADE
);

COMMENT ON TABLE trd_serie_dependencia_productora IS 'Dependencias productoras de una versión de serie TRD (C-4/C8, ADR-040). Contenido de la versión: solo se escribe en borrador; ruta_convalidada se fija una vez al convalidar.';

CREATE INDEX ix_trd_productora_codigo ON trd_serie_dependencia_productora USING btree (dependencia_codigo);

-- ── tipos_documentales ────────────────────────────────────────────────────────

CREATE TABLE tipos_documentales (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    trd_serie_id uuid,
    code character varying(50) NOT NULL,
    nombre text NOT NULL,
    obligatorio boolean DEFAULT false NOT NULL,
    metadata_template text,
    activo boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    admite_ventanilla_publica boolean DEFAULT false NOT NULL,
    publicable boolean DEFAULT false NOT NULL,
    default_response_days integer,
    CONSTRAINT tipos_documentales_default_response_days_check CHECK (((default_response_days >= 1) AND (default_response_days <= 3650))),
    CONSTRAINT tipos_documentales_pkey PRIMARY KEY (id),
    CONSTRAINT tipos_documentales_trd_serie_id_code_key UNIQUE (trd_serie_id, code)
);

COMMENT ON COLUMN tipos_documentales.admite_ventanilla_publica IS 'Si TRUE, este tipo se ofrece en la ventanilla única pública (radicación sin autenticar). Opt-in deliberado: el silencio no significa «todo».';

COMMENT ON COLUMN tipos_documentales.publicable IS 'Si TRUE, los radicados de este tipo pueden publicarse de oficio (Ley 1712/2014 art. 12). Distinto de admite_ventanilla_publica (recepción del público, Ley 1755). Opt-in.';

COMMENT ON COLUMN tipos_documentales.default_response_days IS 'Días hábiles de plazo cuando una Entrada de este tipo llega sin response_days. NULL: no hay plazo por defecto. Lo configura la entidad.';

CREATE INDEX ix_tipos_documentales_publicable ON tipos_documentales USING btree (publicable) WHERE publicable;
CREATE INDEX ix_tipos_documentales_serie ON tipos_documentales USING btree (trd_serie_id);
CREATE INDEX ix_tipos_documentales_ventanilla ON tipos_documentales USING btree (admite_ventanilla_publica) WHERE admite_ventanilla_publica;

-- ── tvd_valoracion ────────────────────────────────────────────────────────────

CREATE TABLE tvd_valoracion (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    trd_serie_id uuid NOT NULL,
    disposicion character varying(2) NOT NULL,
    acta_comite text NOT NULL,
    fecha_acta date NOT NULL,
    fundamento text NOT NULL,
    decidida_por uuid NOT NULL,
    decidida_at timestamp with time zone DEFAULT now() NOT NULL,
    metodo_muestreo text,
    CONSTRAINT tvd_valoracion_acta_comite_check CHECK ((length(btrim(acta_comite)) >= 1)),
    CONSTRAINT tvd_valoracion_disposicion_check CHECK ((disposicion IN ('CT', 'E', 'S', 'M'))),
    CONSTRAINT tvd_valoracion_fundamento_check CHECK ((length(btrim(fundamento)) >= 20)),
    CONSTRAINT tvd_valoracion_pkey PRIMARY KEY (id),
    CONSTRAINT tvd_valoracion_trd_serie_id_key UNIQUE (trd_serie_id),
    CONSTRAINT tvd_valoracion_trd_serie_id_fkey FOREIGN KEY (trd_serie_id) REFERENCES trd_series(id)
);

ALTER TABLE tvd_valoracion
    ADD CONSTRAINT ck_tvd_valoracion_muestreo CHECK ((((disposicion)::text <> 'S'::text) OR (length(btrim(COALESCE(metodo_muestreo, ''::text))) >= 10)));

COMMENT ON TABLE tvd_valoracion IS 'Valoración del Comité sobre una versión de TVD (ADR-043). Append-only: una por versión; `disposicion` congela la decisión (CT/E/S/M) tal como estaba en la serie.';

-- ── Functions ─────────────────────────────────────────────────────────────────

CREATE FUNCTION assert_trd_parent_mismo_tipo() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    parent_tipo   VARCHAR(3);
    parent_parent UUID;
    cambia_padre  BOOLEAN;
BEGIN
    IF NEW.parent_id IS NOT NULL THEN
        SELECT tipo_instrumento, parent_id INTO parent_tipo, parent_parent
          FROM trd_series WHERE id = NEW.parent_id;
        IF parent_tipo IS DISTINCT FROM NEW.tipo_instrumento THEN
            RAISE EXCEPTION 'trd_series: el padre % es de tipo % y la fila es de tipo % — la jerarquia no cruza instrumentos (ADR-026 D2)',
                NEW.parent_id, COALESCE(parent_tipo,'(inexistente)'), NEW.tipo_instrumento;
        END IF;
        IF TG_OP = 'UPDATE' THEN
            cambia_padre := NEW.parent_id IS DISTINCT FROM OLD.parent_id;
        ELSE
            cambia_padre := TRUE;
        END IF;
        IF cambia_padre THEN
            -- Serializa los cambios de forma de la jerarquía del schema: sin el candado, «alta de
            -- una subserie bajo S» y «S pasa a ser subserie» concurrentes comprueban cada una
            -- contra el estado anterior de la otra (aún sin confirmar) y confirman tres niveles.
            PERFORM pg_advisory_xact_lock(hashtext(current_schema() || ':trd_jerarquia'));
            SELECT parent_id INTO parent_parent FROM trd_series WHERE id = NEW.parent_id;
            IF parent_parent IS NOT NULL THEN
                RAISE EXCEPTION 'trd_jerarquia_dos_niveles: el padre % es a su vez una subserie — la jerarquia admite serie > subserie y nada mas (ADR-015)',
                    NEW.parent_id
                    USING ERRCODE = 'check_violation';
            END IF;
            IF EXISTS (SELECT 1 FROM trd_series h WHERE h.parent_id = NEW.id) THEN
                RAISE EXCEPTION 'trd_jerarquia_dos_niveles: la fila % ya tiene subseries y no puede pasar a ser subserie (ADR-015)',
                    NEW.id
                    USING ERRCODE = 'check_violation';
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION reject_trd_productora_mutation() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path FROM CURRENT
    AS $$
DECLARE
    v_id      UUID;
    v_estado  VARCHAR;
    v_parent  UUID;
    v_tipo    VARCHAR(3);
    v_code    VARCHAR;
    v_visible BOOLEAN;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_id := OLD.trd_serie_id;
    ELSE
        v_id := NEW.trd_serie_id;
    END IF;

    SELECT estado, parent_id, tipo_instrumento, code
      INTO v_estado, v_parent, v_tipo, v_code
      FROM trd_series WHERE id = v_id FOR SHARE;
    v_visible := FOUND;

    IF NOT v_visible THEN
        -- DELETE en cascada desde un borrador ya borrado (el append-only de
        -- trd_series solo deja borrar borradores). En INSERT/UPDATE la FK manda.
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'trd_serie_dependencia_productora: la version % no existe', v_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    IF TG_OP <> 'DELETE' THEN
        IF v_parent IS NOT NULL OR v_tipo <> 'TRD' THEN
            RAISE EXCEPTION 'productora_solo_en_serie: la productora se declara en la SERIE TRD, no en subseries ni en TVD (codigo %; C-4/C8)', v_code
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    -- ruta_convalidada: NULL -> valor, una sola vez, y SOLO sobre una versión ya convalidada
    -- (la ruta describe lo que el Consejo aprobó: antes de convalidar no hay nada que congelar).
    IF TG_OP = 'UPDATE'
       AND NEW.trd_serie_id       IS NOT DISTINCT FROM OLD.trd_serie_id
       AND NEW.dependencia_codigo IS NOT DISTINCT FROM OLD.dependencia_codigo
       AND NEW.dependencia_nombre IS NOT DISTINCT FROM OLD.dependencia_nombre
       AND NEW.created_at         IS NOT DISTINCT FROM OLD.created_at
       AND OLD.ruta_convalidada IS NULL AND NEW.ruta_convalidada IS NOT NULL
       AND v_estado = 'convalidada' THEN
        RETURN NEW;
    END IF;

    -- Captura de un dato PREEXISTENTE: una versión `migrada` (backfill, nunca convalidada) puede
    -- recibir su productora al ratificarse. Solo se AÑADE (INSERT): quitarla o cambiarla en esa
    -- versión sigue siendo contenido que exige versión nueva. El servicio la admite una sola vez,
    -- dentro de la tx de la ratificación, y la asienta con su motivo.
    IF TG_OP = 'INSERT' AND v_estado = 'migrada' THEN
        RETURN NEW;
    END IF;

    IF v_estado <> 'borrador' THEN
        RAISE EXCEPTION 'trd_productora_inmutable: la version del codigo % esta en estado % y su dependencia productora es contenido convalidado (ADR-025/040): abra una version nueva',
            v_code, v_estado
            USING ERRCODE = 'check_violation';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION reject_trd_productora_truncate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path FROM CURRENT
    AS $$
BEGIN
    RAISE EXCEPTION 'trd_serie_dependencia_productora es contenido convalidado: TRUNCATE denegado (ADR-040)';
END
$$;

CREATE FUNCTION reject_trd_series_truncate() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'trd_series es append-only: TRUNCATE denegado (ADR-025)';
END;
$$;

CREATE FUNCTION reject_trd_version_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.estado <> 'borrador' THEN
            RAISE EXCEPTION 'trd_series: una version % del codigo % en estado % es append-only (Ac. AGN 001/2024 -- compila 004/2019; ADR-025)',
                OLD.version, OLD.code, OLD.estado;
        END IF;
        RETURN OLD;
    END IF;

    -- ADR-026: inmutable SIEMPRE, sin excepcion de estado.
    IF NEW.tipo_instrumento IS DISTINCT FROM OLD.tipo_instrumento THEN
        RAISE EXCEPTION 'trd_series: `tipo_instrumento` es inmutable (codigo %, version %) — TRD y TVD no se convierten entre si (ADR-026)',
            OLD.code, OLD.version;
    END IF;

    IF OLD.estado <> 'borrador' THEN
        IF NEW.code                  IS DISTINCT FROM OLD.code
        OR NEW.name                  IS DISTINCT FROM OLD.name
        OR NEW.description           IS DISTINCT FROM OLD.description
        OR NEW.parent_id             IS DISTINCT FROM OLD.parent_id
        OR NEW.archivo_gestion_years IS DISTINCT FROM OLD.archivo_gestion_years
        OR NEW.archivo_central_years IS DISTINCT FROM OLD.archivo_central_years
        OR NEW.disposition           IS DISTINCT FROM OLD.disposition
        OR NEW.version               IS DISTINCT FROM OLD.version
        OR NEW.valid_from            IS DISTINCT FROM OLD.valid_from
        OR NEW.fondo_nombre             IS DISTINCT FROM OLD.fondo_nombre
        OR NEW.justificacion_valoracion IS DISTINCT FROM OLD.justificacion_valoracion
        OR NEW.fecha_extrema_inicial    IS DISTINCT FROM OLD.fecha_extrema_inicial
        OR NEW.fecha_extrema_final      IS DISTINCT FROM OLD.fecha_extrema_final THEN
            RAISE EXCEPTION 'trd_series: contenido inmutable en estado % (codigo %, version %) — una modificacion sustantiva crea una version nueva (ADR-025)',
                OLD.estado, OLD.code, OLD.version;
        END IF;
    END IF;

    -- (038) desde `convalidada` el unico destino de `estado` es `derogada`;
    -- acto/fecha/instancia inmutables; rusd/publicacion fill-once.
    IF OLD.estado = 'convalidada' THEN
        IF NEW.estado IS DISTINCT FROM OLD.estado AND NEW.estado <> 'derogada' THEN
            RAISE EXCEPTION 'trd_series: desde convalidada la unica transicion valida es a derogada (codigo %, version %; ADR-025/038)',
                OLD.code, OLD.version;
        END IF;
        IF NEW.acto_administrativo    IS DISTINCT FROM OLD.acto_administrativo
        OR NEW.fecha_convalidacion    IS DISTINCT FROM OLD.fecha_convalidacion
        OR NEW.instancia_convalidante IS DISTINCT FROM OLD.instancia_convalidante THEN
            RAISE EXCEPTION 'trd_series: el acto de convalidacion es inmutable (codigo %, version %; ADR-025/038)',
                OLD.code, OLD.version;
        END IF;
        IF OLD.rusd_radicado IS NOT NULL AND NEW.rusd_radicado IS DISTINCT FROM OLD.rusd_radicado THEN
            RAISE EXCEPTION 'trd_series: rusd_radicado ya registrado es inmutable (codigo %, version %; ADR-025/038)',
                OLD.code, OLD.version;
        END IF;
        IF OLD.fecha_publicacion IS NOT NULL AND NEW.fecha_publicacion IS DISTINCT FROM OLD.fecha_publicacion THEN
            RAISE EXCEPTION 'trd_series: fecha_publicacion ya registrada es inmutable (codigo %, version %; ADR-025/038)',
                OLD.code, OLD.version;
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

CREATE FUNCTION reject_tvd_valoracion_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'tvd_valoracion is append-only (ADR-043)';
END;
$$;

-- ── Triggers ──────────────────────────────────────────────────────────────────

CREATE TRIGGER trg_trd_series_append_only BEFORE DELETE OR UPDATE ON trd_series FOR EACH ROW EXECUTE FUNCTION reject_trd_version_mutation();
CREATE TRIGGER trg_trd_series_no_truncate BEFORE TRUNCATE ON trd_series FOR EACH STATEMENT EXECUTE FUNCTION reject_trd_series_truncate();
CREATE TRIGGER trg_trd_series_parent_tipo BEFORE INSERT OR UPDATE ON trd_series FOR EACH ROW EXECUTE FUNCTION assert_trd_parent_mismo_tipo();

CREATE TRIGGER trg_trd_productora_append_only BEFORE INSERT OR DELETE OR UPDATE ON trd_serie_dependencia_productora FOR EACH ROW EXECUTE FUNCTION reject_trd_productora_mutation();
CREATE TRIGGER trg_trd_productora_no_truncate BEFORE TRUNCATE ON trd_serie_dependencia_productora FOR EACH STATEMENT EXECUTE FUNCTION reject_trd_productora_truncate();

CREATE TRIGGER trg_tvd_valoracion_immutable BEFORE DELETE OR UPDATE ON tvd_valoracion FOR EACH ROW EXECUTE FUNCTION reject_tvd_valoracion_mutation();
CREATE TRIGGER trg_tvd_valoracion_no_truncate BEFORE TRUNCATE ON tvd_valoracion FOR EACH STATEMENT EXECUTE FUNCTION reject_tvd_valoracion_mutation();

-- ── v_trd_series ──────────────────────────────────────────────────────────────

CREATE VIEW v_trd_series AS
 SELECT id,
    code,
    name,
    description,
    disposition,
    is_active,
    created_at,
    parent_id,
    archivo_gestion_years,
    archivo_central_years,
    version,
    valid_from,
    pdfa_profile,
    estado,
    valid_to,
    acto_administrativo,
    fecha_aprobacion_comite,
    fecha_convalidacion,
    instancia_convalidante,
    rusd_radicado,
    fecha_publicacion,
    supersede_a,
    created_by,
    acta_comite,
    tipo_instrumento,
    fondo_nombre,
    fecha_extrema_inicial,
    fecha_extrema_final,
    justificacion_valoracion
   FROM trd_series
  WHERE ((tipo_instrumento)::text = 'TRD'::text)
  WITH CASCADED CHECK OPTION;

-- ── v_tvd_agrupaciones ────────────────────────────────────────────────────────

CREATE VIEW v_tvd_agrupaciones AS
 SELECT id,
    code,
    name,
    description,
    disposition,
    is_active,
    created_at,
    parent_id,
    archivo_gestion_years,
    archivo_central_years,
    version,
    valid_from,
    pdfa_profile,
    estado,
    valid_to,
    acto_administrativo,
    fecha_aprobacion_comite,
    fecha_convalidacion,
    instancia_convalidante,
    rusd_radicado,
    fecha_publicacion,
    supersede_a,
    created_by,
    acta_comite,
    tipo_instrumento,
    fondo_nombre,
    fecha_extrema_inicial,
    fecha_extrema_final,
    justificacion_valoracion
   FROM trd_series
  WHERE ((tipo_instrumento)::text = 'TVD'::text)
  WITH CASCADED CHECK OPTION;
