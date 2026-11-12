-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Physical custody: locations, storage units, movements and loans.

-- ── ubicacion ─────────────────────────────────────────────────────────────────

CREATE TABLE ubicacion (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    parent_id uuid,
    nivel character varying(30) NOT NULL,
    codigo character varying(30) NOT NULL,
    nombre text NOT NULL,
    tipo_archivo character varying(20),
    capacidad_ml numeric(10,2),
    activo boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ubicacion_codigo_key UNIQUE (codigo),
    CONSTRAINT ubicacion_pkey PRIMARY KEY (id),
    CONSTRAINT ubicacion_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES ubicacion(id) ON DELETE CASCADE
);

CREATE INDEX ix_ubicacion_parent ON ubicacion USING btree (parent_id);

-- ── unidad_conservacion ───────────────────────────────────────────────────────

CREATE TABLE unidad_conservacion (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    parent_id uuid,
    ubicacion_id uuid,
    tipo character varying(20) NOT NULL,
    codigo character varying(30) NOT NULL,
    signatura character varying(80),
    soporte character varying(30),
    num_folios integer,
    activo boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT unidad_conservacion_pkey PRIMARY KEY (id),
    CONSTRAINT unidad_conservacion_signatura_key UNIQUE (signatura),
    CONSTRAINT unidad_conservacion_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES unidad_conservacion(id) ON DELETE CASCADE,
    CONSTRAINT unidad_conservacion_ubicacion_id_fkey FOREIGN KEY (ubicacion_id) REFERENCES ubicacion(id) ON DELETE SET NULL
);

CREATE INDEX ix_unidad_parent ON unidad_conservacion USING btree (parent_id);
CREATE INDEX ix_unidad_ubicacion ON unidad_conservacion USING btree (ubicacion_id);

-- ── expediente_unidad ─────────────────────────────────────────────────────────

CREATE TABLE expediente_unidad (
    expediente_id uuid NOT NULL,
    unidad_id uuid NOT NULL,
    folio_inicio integer,
    folio_fin integer,
    added_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT expediente_unidad_pkey PRIMARY KEY (expediente_id, unidad_id),
    CONSTRAINT expediente_unidad_expediente_id_fkey FOREIGN KEY (expediente_id) REFERENCES expedientes(id) ON DELETE CASCADE,
    CONSTRAINT expediente_unidad_unidad_id_fkey FOREIGN KEY (unidad_id) REFERENCES unidad_conservacion(id) ON DELETE CASCADE
);

CREATE INDEX ix_exp_unidad_unidad ON expediente_unidad USING btree (unidad_id);

-- ── movimiento_fisico ─────────────────────────────────────────────────────────

CREATE TABLE movimiento_fisico (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    unidad_id uuid NOT NULL,
    accion character varying(20) NOT NULL,
    ubicacion_before uuid,
    ubicacion_after uuid,
    performed_by text,
    ts timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT movimiento_fisico_pkey PRIMARY KEY (id),
    CONSTRAINT movimiento_fisico_unidad_id_fkey FOREIGN KEY (unidad_id) REFERENCES unidad_conservacion(id) ON DELETE CASCADE
);

CREATE INDEX ix_movimiento_unidad ON movimiento_fisico USING btree (unidad_id, ts);

-- ── prestamo ──────────────────────────────────────────────────────────────────

CREATE TABLE prestamo (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    unidad_id uuid NOT NULL,
    fecha_prestamo timestamp with time zone DEFAULT now() NOT NULL,
    fecha_devolucion_esperada date NOT NULL,
    fecha_devolucion_real timestamp with time zone,
    estado character varying(12) DEFAULT 'prestado'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    prestatario_tipo text NOT NULL,
    prestatario_user_id uuid,
    tercero_nombre text,
    tercero_documento_tipo text,
    tercero_documento_numero text,
    tercero_entidad text,
    tercero_cargo text,
    tercero_correo text,
    tercero_telefono text,
    prestado_por uuid NOT NULL,
    devuelto_recibido_por uuid,
    observaciones text,
    CONSTRAINT ck_prestamo_devolucion_coherente CHECK (((((estado)::text = 'prestado'::text) AND (fecha_devolucion_real IS NULL) AND (devuelto_recibido_por IS NULL)) OR (((estado)::text = 'devuelto'::text) AND (fecha_devolucion_real IS NOT NULL) AND (devuelto_recibido_por IS NOT NULL)))),
    CONSTRAINT ck_prestamo_prestatario_forma CHECK ((((prestatario_tipo = 'interno'::text) AND (prestatario_user_id IS NOT NULL) AND (tercero_nombre IS NULL) AND (tercero_documento_tipo IS NULL) AND (tercero_documento_numero IS NULL) AND (tercero_entidad IS NULL) AND (tercero_cargo IS NULL) AND (tercero_correo IS NULL) AND (tercero_telefono IS NULL)) OR ((prestatario_tipo = 'externo'::text) AND (prestatario_user_id IS NULL) AND (tercero_nombre IS NOT NULL) AND (length(btrim(tercero_nombre)) >= 3) AND (tercero_documento_tipo IS NOT NULL) AND (tercero_documento_numero IS NOT NULL) AND (length(btrim(tercero_documento_numero)) >= 3)))),
    CONSTRAINT ck_prestamo_prestatario_tipo CHECK ((prestatario_tipo = ANY (ARRAY['interno'::text, 'externo'::text]))),
    CONSTRAINT prestamo_estado_check CHECK ((estado IN ('prestado', 'devuelto'))),
    CONSTRAINT prestamo_pkey PRIMARY KEY (id),
    CONSTRAINT prestamo_unidad_id_fkey FOREIGN KEY (unidad_id) REFERENCES unidad_conservacion(id) ON DELETE RESTRICT
);

CREATE INDEX ix_prestamo_estado ON prestamo USING btree (estado, fecha_devolucion_esperada);
CREATE INDEX ix_prestamo_prestatario_user ON prestamo USING btree (prestatario_user_id) WHERE (prestatario_user_id IS NOT NULL);
CREATE INDEX ix_prestamo_unidad ON prestamo USING btree (unidad_id);
CREATE UNIQUE INDEX ux_prestamo_unidad_activo ON prestamo USING btree (unidad_id) WHERE ((estado)::text = 'prestado'::text);

-- ── Functions ─────────────────────────────────────────────────────────────────

CREATE FUNCTION expediente_unidad_prestada(exp uuid) RETURNS text
    LANGUAGE plpgsql
    AS $$
DECLARE
    r uuid;
    prestada uuid;
    cod text;
BEGIN
    FOR r IN
        SELECT DISTINCT COALESCE(unidad_raiz(eu.unidad_id), eu.unidad_id) AS raiz
          FROM expediente_unidad eu WHERE eu.expediente_id = exp ORDER BY 1
    LOOP
        PERFORM unidad_lock_arbol(r);
    END LOOP;
    SELECT unidad_con_prestamo_en_arbol(eu.unidad_id) INTO prestada
      FROM expediente_unidad eu
     WHERE eu.expediente_id = exp AND unidad_con_prestamo_en_arbol(eu.unidad_id) IS NOT NULL
     LIMIT 1;
    IF prestada IS NULL THEN
        RETURN NULL;
    END IF;
    SELECT codigo INTO cod FROM unidad_conservacion WHERE id = prestada;
    RETURN COALESCE(cod, prestada::text);
END;
$$;

CREATE FUNCTION prestamo_guard_insert() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.estado <> 'prestado' THEN
        RETURN NEW;   -- una fila ya devuelta no saca nada del archivo
    END IF;
    PERFORM unidad_lock_arbol(NEW.unidad_id);
    IF unidad_con_prestamo_en_arbol(NEW.unidad_id) IS NOT NULL THEN
        RAISE EXCEPTION 'unidad_ya_prestada'
            USING ERRCODE = '23505', CONSTRAINT = 'ux_prestamo_arbol_activo';
    END IF;
    IF EXISTS (SELECT 1 FROM transferencias t
                 JOIN expediente_unidad eu ON eu.expediente_id = t.expediente_id
                WHERE t.estado IN ('preparada','enviada')
                  AND eu.unidad_id IN (SELECT unidad_arbol(NEW.unidad_id))) THEN
        RAISE EXCEPTION 'unidad_en_transferencia'
            USING ERRCODE = '23514', CONSTRAINT = 'ck_prestamo_unidad_en_transferencia';
    END IF;
    IF EXISTS (SELECT 1 FROM eliminacion_lote_items i
                 JOIN eliminacion_lotes l ON l.id = i.lote_id
                 JOIN expediente_unidad eu ON eu.expediente_id = i.expediente_id
                WHERE l.estado IN ('borrador','publicado','avalado')
                  AND eu.unidad_id IN (SELECT unidad_arbol(NEW.unidad_id))) THEN
        RAISE EXCEPTION 'unidad_en_lote_eliminacion'
            USING ERRCODE = '23514', CONSTRAINT = 'ck_prestamo_unidad_en_lote';
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION prestamo_guard_mutacion() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'prestamo_no_se_borra'
            USING ERRCODE = '23514', CONSTRAINT = 'ck_prestamo_append_only';
    END IF;
    IF OLD.estado = 'prestado' AND NEW.estado = 'devuelto'
       AND (NEW.id, NEW.unidad_id, NEW.prestatario_tipo, NEW.prestatario_user_id,
            NEW.tercero_nombre, NEW.tercero_documento_tipo, NEW.tercero_documento_numero,
            NEW.tercero_entidad, NEW.tercero_cargo, NEW.tercero_correo, NEW.tercero_telefono,
            NEW.prestado_por, NEW.fecha_prestamo, NEW.fecha_devolucion_esperada,
            NEW.observaciones, NEW.created_at)
           IS NOT DISTINCT FROM
           (OLD.id, OLD.unidad_id, OLD.prestatario_tipo, OLD.prestatario_user_id,
            OLD.tercero_nombre, OLD.tercero_documento_tipo, OLD.tercero_documento_numero,
            OLD.tercero_entidad, OLD.tercero_cargo, OLD.tercero_correo, OLD.tercero_telefono,
            OLD.prestado_por, OLD.fecha_prestamo, OLD.fecha_devolucion_esperada,
            OLD.observaciones, OLD.created_at) THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'prestamo_solo_se_devuelve'
        USING ERRCODE = '23514', CONSTRAINT = 'ck_prestamo_append_only';
END;
$$;

CREATE FUNCTION prestamo_guard_truncate() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'prestamo_no_se_borra'
        USING ERRCODE = '23514', CONSTRAINT = 'ck_prestamo_append_only';
END;
$$;

CREATE FUNCTION ubicacion_guard_transferencia_recibida() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM transferencias t
                WHERE t.estado = 'recibida'
                  AND (t.ubicacion_origen_id = OLD.id OR t.ubicacion_destino_id = OLD.id)) THEN
        RAISE EXCEPTION 'ubicacion_con_transferencia_recibida'
            USING ERRCODE = '23514', CONSTRAINT = 'ck_ubicacion_traza_transferencia_recibida';
    END IF;
    RETURN OLD;
END;
$$;

CREATE FUNCTION unidad_arbol(p uuid) RETURNS SETOF uuid
    LANGUAGE sql STABLE
    AS $$
    WITH RECURSIVE d(id) AS (
        SELECT id FROM unidad_conservacion WHERE id = p
        UNION
        SELECT u.id FROM unidad_conservacion u JOIN d ON u.parent_id = d.id
    )
    SELECT id FROM d
$$;

CREATE FUNCTION unidad_con_prestamo_en_arbol(p uuid) RETURNS uuid
    LANGUAGE sql STABLE
    AS $$
    WITH RECURSIVE anc(id, parent_id) AS (
        SELECT id, parent_id FROM unidad_conservacion WHERE id = p
        UNION
        SELECT u.id, u.parent_id FROM unidad_conservacion u JOIN anc a ON u.id = a.parent_id
    )
    SELECT pr.unidad_id
      FROM prestamo pr
     WHERE pr.estado = 'prestado'
       AND (pr.unidad_id IN (SELECT id FROM anc) OR pr.unidad_id IN (SELECT unidad_arbol(p)))
     LIMIT 1
$$;

CREATE FUNCTION unidad_raiz(p uuid) RETURNS uuid
    LANGUAGE sql STABLE
    AS $$
    WITH RECURSIVE anc(id, parent_id) AS (
        SELECT id, parent_id FROM unidad_conservacion WHERE id = p
        UNION
        SELECT u.id, u.parent_id FROM unidad_conservacion u JOIN anc a ON u.id = a.parent_id
    )
    SELECT id FROM anc WHERE parent_id IS NULL LIMIT 1
$$;

-- ── Triggers ──────────────────────────────────────────────────────────────────

CREATE TRIGGER trg_ubicacion_guard_transferencia_recibida BEFORE DELETE ON ubicacion FOR EACH ROW EXECUTE FUNCTION ubicacion_guard_transferencia_recibida();

CREATE TRIGGER trg_prestamo_guard_insert BEFORE INSERT ON prestamo FOR EACH ROW EXECUTE FUNCTION prestamo_guard_insert();
CREATE TRIGGER trg_prestamo_guard_mutacion BEFORE DELETE OR UPDATE ON prestamo FOR EACH ROW EXECUTE FUNCTION prestamo_guard_mutacion();
CREATE TRIGGER trg_prestamo_guard_truncate BEFORE TRUNCATE ON prestamo FOR EACH STATEMENT EXECUTE FUNCTION prestamo_guard_truncate();
