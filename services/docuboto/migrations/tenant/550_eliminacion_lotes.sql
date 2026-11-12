-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Disposal: decisions, batches and their acts.

-- ── disposicion_decisiones ────────────────────────────────────────────────────

CREATE TABLE disposicion_decisiones (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    expediente_id uuid NOT NULL,
    fase text NOT NULL,
    decision text NOT NULL,
    fecha_vencimiento date NOT NULL,
    disposition_code text,
    motivo text,
    revisar_de_nuevo_el date,
    decidido_por uuid NOT NULL,
    decidido_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT disposicion_decisiones_aplazada_forma_check CHECK (((decision <> 'aplazada'::text) OR ((motivo IS NOT NULL) AND (length(btrim(motivo)) >= 10) AND (revisar_de_nuevo_el IS NOT NULL)))),
    CONSTRAINT disposicion_decisiones_decision_check CHECK ((decision = ANY (ARRAY['confirmada'::text, 'aplazada'::text]))),
    CONSTRAINT disposicion_decisiones_fase_check CHECK ((fase = ANY (ARRAY['gestion'::text, 'central'::text]))),
    CONSTRAINT disposicion_decisiones_pkey PRIMARY KEY (id)
);

CREATE INDEX ix_disposicion_decisiones_exp ON disposicion_decisiones USING btree (expediente_id, fase, fecha_vencimiento, decidido_at DESC);

-- ── eliminacion_lotes ─────────────────────────────────────────────────────────

CREATE TABLE eliminacion_lotes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    codigo text NOT NULL,
    estado text DEFAULT 'borrador'::text NOT NULL,
    observaciones text,
    creado_por uuid NOT NULL,
    creado_at timestamp with time zone DEFAULT now() NOT NULL,
    publicado_at timestamp with time zone,
    publicado_hasta date,
    acta_comite text,
    fecha_aval date,
    acto_administrativo text,
    avalado_por uuid,
    avalado_at timestamp with time zone,
    ejecutado_at timestamp with time zone,
    ejecutado_por uuid,
    anulado_at timestamp with time zone,
    anulado_por uuid,
    anulado_motivo text,
    CONSTRAINT eliminacion_lotes_anulado_forma_check CHECK (((estado <> 'anulado'::text) OR ((anulado_at IS NOT NULL) AND (anulado_por IS NOT NULL) AND (anulado_motivo IS NOT NULL) AND (length(btrim(anulado_motivo)) >= 10)))),
    CONSTRAINT eliminacion_lotes_avalado_forma_check CHECK (((estado <> ALL (ARRAY['avalado'::text, 'ejecutado'::text])) OR ((acta_comite IS NOT NULL) AND (fecha_aval IS NOT NULL) AND (acto_administrativo IS NOT NULL) AND (avalado_por IS NOT NULL)))),
    CONSTRAINT eliminacion_lotes_estado_check CHECK ((estado = ANY (ARRAY['borrador'::text, 'publicado'::text, 'avalado'::text, 'ejecutado'::text, 'anulado'::text]))),
    CONSTRAINT eliminacion_lotes_publicado_forma_check CHECK (((estado <> 'publicado'::text) OR ((publicado_at IS NOT NULL) AND (publicado_hasta IS NOT NULL)))),
    CONSTRAINT eliminacion_lotes_codigo_key UNIQUE (codigo),
    CONSTRAINT eliminacion_lotes_pkey PRIMARY KEY (id)
);

CREATE INDEX ix_eliminacion_lotes_estado ON eliminacion_lotes USING btree (estado, creado_at DESC);

-- ── eliminacion_lote_items ────────────────────────────────────────────────────

CREATE TABLE eliminacion_lote_items (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    lote_id uuid NOT NULL,
    expediente_id uuid NOT NULL,
    expediente_code text NOT NULL,
    expediente_nombre text,
    trd_serie_code text,
    disposition_code text NOT NULL,
    fecha_vencimiento date NOT NULL,
    fecha_extrema_inicial date,
    fecha_extrema_final date,
    nivel_seguridad smallint NOT NULL,
    radicados_incluidos integer,
    worm_retain_until timestamp with time zone,
    CONSTRAINT eliminacion_lote_items_pkey PRIMARY KEY (id),
    CONSTRAINT eliminacion_lote_items_unicos UNIQUE (lote_id, expediente_id),
    CONSTRAINT eliminacion_lote_items_lote_id_fkey FOREIGN KEY (lote_id) REFERENCES eliminacion_lotes(id) ON DELETE CASCADE
);

CREATE INDEX ix_eliminacion_lote_items_exp ON eliminacion_lote_items USING btree (expediente_id);

-- ── eliminacion_lote_soporte_citado ───────────────────────────────────────────

CREATE TABLE eliminacion_lote_soporte_citado (
    lote_id uuid NOT NULL,
    expediente_id uuid NOT NULL,
    excepcion_id uuid NOT NULL,
    transferencia_id uuid NOT NULL,
    rol text NOT NULL,
    ordinal integer NOT NULL,
    file_id uuid NOT NULL,
    anexo_sha256 text NOT NULL,
    congelado_at timestamp with time zone DEFAULT now() NOT NULL,
    soporte_eliminado_at timestamp with time zone,
    CONSTRAINT eliminacion_lote_soporte_citado_pkey PRIMARY KEY (lote_id, excepcion_id),
    CONSTRAINT eliminacion_lote_soporte_citado_excepcion_id_fkey FOREIGN KEY (excepcion_id) REFERENCES transferencia_acta_firma_excepcion(id),
    CONSTRAINT eliminacion_lote_soporte_citado_lote_id_fkey FOREIGN KEY (lote_id) REFERENCES eliminacion_lotes(id) ON DELETE CASCADE
);

COMMENT ON TABLE eliminacion_lote_soporte_citado IS 'Baja de soporte (ADR-043): constancias de firma inobtenible vigentes cuyo anexo de soporte se destruirá con el lote. Congelada al proponer; el aval del lote la hace efectiva para v_anexo_citado_vigente.';

CREATE INDEX ix_lote_soporte_citado_excepcion ON eliminacion_lote_soporte_citado USING btree (excepcion_id);
CREATE INDEX ix_lote_soporte_citado_expediente ON eliminacion_lote_soporte_citado USING btree (lote_id, expediente_id);

-- ── eliminacion_acta ──────────────────────────────────────────────────────────

CREATE TABLE eliminacion_acta (
    lote_id uuid NOT NULL,
    acta_xml text NOT NULL,
    acta_sha256 text NOT NULL,
    metodo_borrado text NOT NULL,
    observaciones text,
    generado_por uuid NOT NULL,
    generado_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT eliminacion_acta_metodo_forma_check CHECK ((length(btrim(metodo_borrado)) >= 10)),
    CONSTRAINT eliminacion_acta_sha_forma_check CHECK ((acta_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT eliminacion_acta_pkey PRIMARY KEY (lote_id),
    CONSTRAINT eliminacion_acta_lote_id_fkey FOREIGN KEY (lote_id) REFERENCES eliminacion_lotes(id) ON DELETE CASCADE
);

COMMENT ON TABLE eliminacion_acta IS 'Acta de eliminación documental (E04, RF-TRD-07 fase 4) -- CONSERVACION PERMANENTE: sobrevive a lo eliminado y no se elimina jamás. Append-only por trigger.';

-- ── eliminacion_acta_firma ────────────────────────────────────────────────────

CREATE TABLE eliminacion_acta_firma (
    lote_id uuid NOT NULL,
    estado text DEFAULT 'pendiente_firma'::text NOT NULL,
    firma_id uuid,
    minio_key_firmado text,
    xades_level text,
    acreditado boolean DEFAULT false NOT NULL,
    last_error text,
    reconcile_intentos integer DEFAULT 0 NOT NULL,
    reconcile_proximo_at timestamp with time zone,
    reconcile_agotado boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    firmado_at timestamp with time zone,
    worm_protegido_at timestamp with time zone,
    worm_object_id uuid,
    worm_retain_until timestamp with time zone,
    worm_last_error text,
    worm_reconcile_intentos integer DEFAULT 0 NOT NULL,
    worm_reconcile_proximo_at timestamp with time zone,
    worm_reconcile_agotado boolean DEFAULT false NOT NULL,
    CONSTRAINT eliminacion_acta_firma_estado_check CHECK ((estado = ANY (ARRAY['pendiente_firma'::text, 'firmado'::text]))),
    CONSTRAINT eliminacion_acta_firma_forma_check CHECK (((estado <> 'firmado'::text) OR ((firma_id IS NOT NULL) AND (minio_key_firmado IS NOT NULL) AND (firmado_at IS NOT NULL)))),
    CONSTRAINT eliminacion_acta_firma_pkey PRIMARY KEY (lote_id),
    CONSTRAINT eliminacion_acta_firma_lote_id_fkey FOREIGN KEY (lote_id) REFERENCES eliminacion_acta(lote_id) ON DELETE CASCADE
);

COMMENT ON TABLE eliminacion_acta_firma IS 'Estado MUTABLE del sellado XAdES del acta de eliminación -- 1:1 con eliminacion_acta (append-only), NUNCA la misma tabla.';

COMMENT ON COLUMN eliminacion_acta_firma.worm_retain_until IS 'Fin de la retención Object-Lock del acta. Conservación PERMANENTE se materializa como el máximo expresable (100 años) porque Object-Lock exige una fecha, no un infinito; es renovable por la misma vía que las series CT.';

CREATE INDEX ix_eliminacion_acta_firma_pendiente ON eliminacion_acta_firma USING btree (reconcile_proximo_at) WHERE ((estado = 'pendiente_firma'::text) AND (reconcile_agotado = false));
CREATE INDEX ix_eliminacion_acta_firma_worm_pendiente ON eliminacion_acta_firma USING btree (worm_reconcile_proximo_at) WHERE ((estado = 'firmado'::text) AND (worm_protegido_at IS NULL) AND (worm_reconcile_agotado = false));

-- ── Foreign keys from tables created earlier ──────────────────────────────────

ALTER TABLE ONLY expediente_lapida
    ADD CONSTRAINT expediente_lapida_lote_id_fkey FOREIGN KEY (lote_id) REFERENCES eliminacion_lotes(id);

-- ── Functions ─────────────────────────────────────────────────────────────────

CREATE FUNCTION guard_lote_soporte_citado() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF pg_trigger_depth() > 1 THEN
            RETURN OLD;  -- cascada desde eliminacion_lotes / expedientes
        END IF;
        RAISE EXCEPTION 'eliminacion_lote_soporte_citado es append-only (ADR-043)';
    END IF;
    IF OLD.soporte_eliminado_at IS NOT NULL
       OR NEW.soporte_eliminado_at IS NULL
       OR (to_jsonb(NEW) - 'soporte_eliminado_at') IS DISTINCT FROM (to_jsonb(OLD) - 'soporte_eliminado_at') THEN
        RAISE EXCEPTION 'eliminacion_lote_soporte_citado: el unico UPDATE admitido fija soporte_eliminado_at (de NULL a un valor)';
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION reject_disposicion_decisiones_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'disposicion_decisiones is append-only (Ley 594/2000)';
END;
$$;

CREATE FUNCTION reject_eliminacion_acta_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION
        'eliminacion_acta es de conservación permanente y no admite % (E04, RF-TRD-07)',
        TG_OP;
END;
$$;

CREATE FUNCTION reject_eliminacion_item_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    estado_lote TEXT;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'eliminacion_lote_items no se edita: el inventario se congela al proponer';
    END IF;
    SELECT estado INTO estado_lote FROM eliminacion_lotes WHERE id = OLD.lote_id;
    -- Un lote borrado en cascada (`ON DELETE CASCADE`) ya no tiene fila que
    -- consultar: se permite, porque el lote entero se está yendo.
    IF estado_lote IS NOT NULL AND estado_lote <> 'borrador' THEN
        RAISE EXCEPTION
            'el inventario de un lote % ya no se puede modificar (se congela al publicar)',
            estado_lote;
    END IF;
    RETURN OLD;
END;
$$;

CREATE FUNCTION reject_eliminacion_lote_delete_avalado() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF OLD.estado IN ('avalado', 'ejecutado') THEN
        RAISE EXCEPTION 'un lote de eliminación % no se borra: su inventario y su baja de soporte son la evidencia de lo destruido',
            OLD.estado;
    END IF;
    RETURN OLD;
END;
$$;

CREATE FUNCTION reject_lote_soporte_citado_truncate() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'eliminacion_lote_soporte_citado es append-only: TRUNCATE denegado';
END;
$$;

-- ── Triggers ──────────────────────────────────────────────────────────────────

CREATE TRIGGER trg_disposicion_decisiones_immutable BEFORE DELETE OR UPDATE ON disposicion_decisiones FOR EACH ROW EXECUTE FUNCTION reject_disposicion_decisiones_mutation();

CREATE TRIGGER trg_eliminacion_lotes_no_borrar_avalado BEFORE DELETE ON eliminacion_lotes FOR EACH ROW EXECUTE FUNCTION reject_eliminacion_lote_delete_avalado();

CREATE TRIGGER trg_eliminacion_items_congelado BEFORE DELETE OR UPDATE ON eliminacion_lote_items FOR EACH ROW EXECUTE FUNCTION reject_eliminacion_item_mutation();

CREATE TRIGGER trg_lote_soporte_citado_guard BEFORE DELETE OR UPDATE ON eliminacion_lote_soporte_citado FOR EACH ROW EXECUTE FUNCTION guard_lote_soporte_citado();
CREATE TRIGGER trg_lote_soporte_citado_no_truncate BEFORE TRUNCATE ON eliminacion_lote_soporte_citado FOR EACH STATEMENT EXECUTE FUNCTION reject_lote_soporte_citado_truncate();

CREATE TRIGGER trg_eliminacion_acta_append_only BEFORE DELETE OR UPDATE ON eliminacion_acta FOR EACH ROW EXECUTE FUNCTION reject_eliminacion_acta_mutation();

-- ── v_anexo_citado_vigente ────────────────────────────────────────────────────

CREATE VIEW v_anexo_citado_vigente AS
 SELECT referencia_anexo_id AS file_id,
    id AS excepcion_id,
    transferencia_id,
    rol,
    ordinal
   FROM transferencia_acta_firma_excepcion e
  WHERE ((referencia_anexo_id IS NOT NULL) AND (NOT (EXISTS ( SELECT 1
           FROM transferencia_acta_firma_excepcion_revocacion r
          WHERE (r.excepcion_id = e.id)))) AND (NOT (EXISTS ( SELECT 1
           FROM (eliminacion_lote_soporte_citado s
             JOIN eliminacion_lotes l ON ((l.id = s.lote_id)))
          WHERE ((s.excepcion_id = e.id) AND (l.estado = ANY (ARRAY['avalado'::text, 'ejecutado'::text])))))));

COMMENT ON VIEW v_anexo_citado_vigente IS 'CONTRATO con storage-service (ADR-043): anexos (files.id) citados por una constancia de firma inobtenible vigente. Storage la consulta, en sentencia aparte tras bloquear la fila de files, antes de purgar. No renombrar ni quitar columnas sin migrar storage.';
