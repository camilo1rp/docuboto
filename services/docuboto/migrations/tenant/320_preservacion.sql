-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Preservation: plans, migrations, events, legal holds and WORM objects.

-- ── preservacion_plan ─────────────────────────────────────────────────────────

CREATE TABLE preservacion_plan (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    version integer NOT NULL,
    formatos_destino text[] DEFAULT '{}'::text[] NOT NULL,
    num_copias integer DEFAULT 1 NOT NULL,
    periodicidad_fixity_dias integer DEFAULT 180 NOT NULL,
    politica_migracion text,
    contingencia text,
    creado_por text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    formatos_origen text[] DEFAULT '{}'::text[] NOT NULL,
    CONSTRAINT preservacion_plan_pkey PRIMARY KEY (id),
    CONSTRAINT preservacion_plan_version_key UNIQUE (version)
);

COMMENT ON COLUMN preservacion_plan.formatos_origen IS 'Tipos MIME que la entidad declara en riesgo de obsolescencia (RF-PRE-04). Vacío = política no declarada en el plan.';

-- ── preservacion_migracion ────────────────────────────────────────────────────

CREATE TABLE preservacion_migracion (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    file_id_origen uuid NOT NULL,
    file_id_destino uuid,
    formato_origen text NOT NULL,
    formato_destino text NOT NULL,
    sha256_origen character(64) NOT NULL,
    sha256_destino character(64),
    herramienta text NOT NULL,
    estado text NOT NULL,
    motivo text,
    iniciada_por uuid,
    iniciada_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT preservacion_migracion_estado_check CHECK ((estado = ANY (ARRAY['migrado'::text, 'fallido'::text, 'no_verificado'::text]))),
    CONSTRAINT preservacion_migracion_forma_check CHECK (((estado <> 'migrado'::text) OR ((file_id_destino IS NOT NULL) AND (sha256_destino ~ '^[0-9a-f]{64}$'::text)))),
    CONSTRAINT preservacion_migracion_sha_origen_check CHECK ((sha256_origen ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT preservacion_migracion_pkey PRIMARY KEY (id)
);

COMMENT ON TABLE preservacion_migracion IS 'Cadena de procedencia de una migración de formato (E10, RF-PRE-04): origen y destino, hashes antes y después, herramienta y desenlace. El original NUNCA se reemplaza.';

COMMENT ON COLUMN preservacion_migracion.estado IS 'migrado (derivado producido Y verificado conforme) | fallido (no se pudo convertir, o el resultado no era conforme) | no_verificado (se convirtió pero no había con qué comprobar la conformidad -- NO se promueve a copia de preservación).';

CREATE INDEX ix_preservacion_migracion_origen ON preservacion_migracion USING btree (file_id_origen, iniciada_at DESC);
CREATE UNIQUE INDEX ux_preservacion_migracion_vigente ON preservacion_migracion USING btree (file_id_origen, formato_destino) WHERE (estado = 'migrado'::text);

-- ── preservacion_evento ───────────────────────────────────────────────────────

CREATE TABLE preservacion_evento (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    object_ref text NOT NULL,
    tipo character varying(24) NOT NULL,
    resultado character varying(16) DEFAULT 'ok'::character varying NOT NULL,
    hash_before character(64),
    hash_after character(64),
    detalle jsonb,
    agente text,
    ts timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT preservacion_evento_resultado_check CHECK ((resultado IN ('ok', 'fallo', 'no_evaluado'))),
    CONSTRAINT preservacion_evento_pkey PRIMARY KEY (id)
);

CREATE INDEX ix_preservacion_evento_obj ON preservacion_evento USING btree (object_ref, ts);

-- ── preservacion_legal_hold ───────────────────────────────────────────────────

CREATE TABLE preservacion_legal_hold (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    expediente_id text NOT NULL,
    motivo_tipo text NOT NULL,
    fundamento text NOT NULL,
    acta_ref text NOT NULL,
    fijado_por uuid,
    fijado_at timestamp with time zone DEFAULT now() NOT NULL,
    levantado_por uuid,
    levantado_at timestamp with time zone,
    levantado_fundamento text,
    levantado_acta_ref text,
    CONSTRAINT ck_plh_fijado_por CHECK (((motivo_tipo = 'valoracion_pendiente'::text) OR (fijado_por IS NOT NULL))),
    CONSTRAINT ck_plh_fundamento CHECK ((length(btrim(fundamento)) >= 10)),
    CONSTRAINT ck_plh_levantamiento CHECK ((((levantado_at IS NULL) AND (levantado_fundamento IS NULL) AND (levantado_acta_ref IS NULL)) OR ((levantado_at IS NOT NULL) AND (levantado_fundamento IS NOT NULL) AND (levantado_acta_ref IS NOT NULL)))),
    CONSTRAINT ck_plh_motivo CHECK ((motivo_tipo = ANY (ARRAY['judicial'::text, 'disciplinario'::text, 'fiscal'::text, 'administrativo'::text, 'ddhh_memoria_historica'::text, 'valoracion_pendiente'::text]))),
    CONSTRAINT preservacion_legal_hold_pkey PRIMARY KEY (id)
);

COMMENT ON TABLE preservacion_legal_hold IS 'Retenciones legales por MOTIVO (C-5/C7). El S3 Object Lock del objeto está en hold mientras exista >= 1 fila activa del expediente.';

CREATE INDEX ix_plh_expediente_activo ON preservacion_legal_hold USING btree (expediente_id) WHERE (levantado_at IS NULL);
CREATE UNIQUE INDEX ux_plh_valoracion_activa ON preservacion_legal_hold USING btree (expediente_id) WHERE ((motivo_tipo = 'valoracion_pendiente'::text) AND (levantado_at IS NULL));

-- ── preservacion_worm_objeto ──────────────────────────────────────────────────

CREATE TABLE preservacion_worm_objeto (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    expediente_id text NOT NULL,
    tipo text NOT NULL,
    bucket text NOT NULL,
    object_key text NOT NULL,
    version_id text,
    sha256 character(64) NOT NULL,
    retain_until timestamp with time zone,
    retention_mode text,
    legal_hold boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    file_id uuid,
    renovado_at timestamp with time zone,
    renovaciones integer DEFAULT 0 NOT NULL,
    objeto_tipo text DEFAULT 'expediente'::text NOT NULL,
    renueva_a uuid,
    retencion_origen text DEFAULT 'calendario'::text NOT NULL,
    CONSTRAINT ck_pwo_diferida_exige_hold CHECK (((retencion_origen <> 'valoracion_pendiente'::text) OR legal_hold)),
    CONSTRAINT ck_pwo_retencion_coherente CHECK ((((retencion_origen = 'calendario'::text) = ((retain_until IS NOT NULL) AND (retention_mode IS NOT NULL))) AND ((retain_until IS NULL) = (retention_mode IS NULL)))),
    CONSTRAINT ck_pwo_retencion_origen CHECK ((retencion_origen = ANY (ARRAY['calendario'::text, 'valoracion_pendiente'::text, 'valorada_sin_retencion'::text]))),
    CONSTRAINT preservacion_worm_objeto_objeto_tipo_check CHECK ((objeto_tipo = ANY (ARRAY['expediente'::text, 'lote'::text]))),
    CONSTRAINT preservacion_worm_objeto_pareja_check CHECK ((((tipo = 'acta_eliminacion'::text) AND (objeto_tipo = 'lote'::text)) OR ((tipo <> 'acta_eliminacion'::text) AND (objeto_tipo = 'expediente'::text)))),
    CONSTRAINT preservacion_worm_objeto_tipo_check CHECK ((tipo = ANY (ARRAY['indice'::text, 'aip'::text, 'acta_transferencia'::text, 'acta_eliminacion'::text, 'acta_firma_elabora'::text, 'acta_firma_entrega'::text, 'acta_firma_recibe'::text, 'acta_firma_excepcion_elabora'::text, 'acta_firma_excepcion_entrega'::text, 'acta_firma_excepcion_recibe'::text, 'acta_firma_excepcion_personal_elabora'::text, 'acta_firma_excepcion_personal_entrega'::text, 'acta_firma_excepcion_personal_recibe'::text, 'acta_firma_revocacion_elabora'::text, 'acta_firma_revocacion_entrega'::text, 'acta_firma_revocacion_recibe'::text, 'acta_firma_revocacion_personal_elabora'::text, 'acta_firma_revocacion_personal_entrega'::text, 'acta_firma_revocacion_personal_recibe'::text, 'acta_firma_excepcion_sobre_elabora'::text, 'acta_firma_excepcion_sobre_entrega'::text, 'acta_firma_excepcion_sobre_recibe'::text, 'acta_firma_revocacion_sobre_elabora'::text, 'acta_firma_revocacion_sobre_entrega'::text, 'acta_firma_revocacion_sobre_recibe'::text, 'firma_lta_renovada'::text]))),
    CONSTRAINT preservacion_worm_objeto_pkey PRIMARY KEY (id),
    CONSTRAINT preservacion_worm_objeto_renueva_a_fkey FOREIGN KEY (renueva_a) REFERENCES preservacion_worm_objeto(id)
);

COMMENT ON COLUMN preservacion_worm_objeto.expediente_id IS 'ID del PROPIETARIO del artefacto (expediente o lote, según objeto_tipo). Conserva su nombre histórico de la migración 006; ver la cabecera de la 014.';

COMMENT ON COLUMN preservacion_worm_objeto.file_id IS 'file_id de `files` cuyo contenido quedó protegido bajo WORM (Inc.1, cableado) — antes solo se registraba la resolución física bucket/object_key, sin la clave de negocio.';

COMMENT ON COLUMN preservacion_worm_objeto.renovado_at IS 'Instante de la última extensión CONFIRMADA de retain_until (E10 debt, renovación CT). NULL si el objeto nunca fue renovado.';

COMMENT ON COLUMN preservacion_worm_objeto.renovaciones IS 'Número de extensiones CONFIRMADAS de retain_until (no cuenta llamadas no-op, ver PreservationWormService.renovar_retencion).';

COMMENT ON COLUMN preservacion_worm_objeto.objeto_tipo IS 'De qué es `expediente_id`: expediente (todo el contenido y el acta de transferencia) | lote (acta de eliminación, E04/RF-TRD-07).';

COMMENT ON COLUMN preservacion_worm_objeto.retencion_origen IS 'calendario | valoracion_pendiente (retain_until NULL + legal hold) | valorada_sin_retencion. C-5/C7, ADR-043.';

CREATE INDEX ix_preservacion_worm_expediente ON preservacion_worm_objeto USING btree (expediente_id);
CREATE UNIQUE INDEX ux_preservacion_worm_objeto_acta_firma_elabora ON preservacion_worm_objeto USING btree (expediente_id) WHERE (tipo = 'acta_firma_elabora'::text);
CREATE UNIQUE INDEX ux_preservacion_worm_objeto_acta_firma_entrega ON preservacion_worm_objeto USING btree (expediente_id) WHERE (tipo = 'acta_firma_entrega'::text);
CREATE UNIQUE INDEX ux_preservacion_worm_objeto_acta_firma_recibe ON preservacion_worm_objeto USING btree (expediente_id) WHERE (tipo = 'acta_firma_recibe'::text);
CREATE UNIQUE INDEX ux_preservacion_worm_objeto_expediente_acta ON preservacion_worm_objeto USING btree (expediente_id) WHERE (tipo = 'acta_transferencia'::text);
CREATE UNIQUE INDEX ux_preservacion_worm_objeto_expediente_aip_sha256 ON preservacion_worm_objeto USING btree (expediente_id, sha256) WHERE (tipo = 'aip'::text);
CREATE UNIQUE INDEX ux_preservacion_worm_objeto_expediente_indice ON preservacion_worm_objeto USING btree (expediente_id) WHERE (tipo = 'indice'::text);
CREATE UNIQUE INDEX ux_preservacion_worm_objeto_lote_acta_eliminacion ON preservacion_worm_objeto USING btree (expediente_id) WHERE (tipo = 'acta_eliminacion'::text);
CREATE UNIQUE INDEX ux_pwo_aip_raiz ON preservacion_worm_objeto USING btree (expediente_id) WHERE ((tipo = 'aip'::text) AND (renueva_a IS NULL));
CREATE UNIQUE INDEX ux_pwo_firma_lta_renovada_file ON preservacion_worm_objeto USING btree (tipo, file_id) WHERE (tipo = 'firma_lta_renovada'::text);
CREATE UNIQUE INDEX ux_pwo_multi_artefacto_file ON preservacion_worm_objeto USING btree (expediente_id, tipo, file_id) WHERE ((file_id IS NOT NULL) AND (tipo = ANY (ARRAY['acta_firma_excepcion_elabora'::text, 'acta_firma_excepcion_entrega'::text, 'acta_firma_excepcion_recibe'::text, 'acta_firma_excepcion_personal_elabora'::text, 'acta_firma_excepcion_personal_entrega'::text, 'acta_firma_excepcion_personal_recibe'::text, 'acta_firma_revocacion_elabora'::text, 'acta_firma_revocacion_entrega'::text, 'acta_firma_revocacion_recibe'::text, 'acta_firma_revocacion_personal_elabora'::text, 'acta_firma_revocacion_personal_entrega'::text, 'acta_firma_revocacion_personal_recibe'::text, 'acta_firma_excepcion_sobre_elabora'::text, 'acta_firma_excepcion_sobre_entrega'::text, 'acta_firma_excepcion_sobre_recibe'::text, 'acta_firma_revocacion_sobre_elabora'::text, 'acta_firma_revocacion_sobre_entrega'::text, 'acta_firma_revocacion_sobre_recibe'::text])));

-- ── Functions ─────────────────────────────────────────────────────────────────

CREATE FUNCTION reject_plh_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF OLD.levantado_at IS NULL
           AND NEW.levantado_at IS NOT NULL
           AND NEW.id = OLD.id
           AND NEW.expediente_id = OLD.expediente_id
           AND NEW.motivo_tipo = OLD.motivo_tipo
           AND NEW.fundamento = OLD.fundamento
           AND NEW.acta_ref = OLD.acta_ref
           AND NEW.fijado_por IS NOT DISTINCT FROM OLD.fijado_por
           AND NEW.fijado_at = OLD.fijado_at THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'preservacion_legal_hold: el único UPDATE admitido es levantar un hold activo (una vez)';
    END IF;
    RAISE EXCEPTION 'preservacion_legal_hold es append-only (Ley 594/2000): % rechazado', TG_OP;
END;
$$;

-- ── Triggers ──────────────────────────────────────────────────────────────────

CREATE TRIGGER trg_plh_no_mutar BEFORE DELETE OR UPDATE ON preservacion_legal_hold FOR EACH ROW EXECUTE FUNCTION reject_plh_mutation();
CREATE TRIGGER trg_plh_no_truncar BEFORE TRUNCATE ON preservacion_legal_hold FOR EACH STATEMENT EXECUTE FUNCTION reject_plh_mutation();
