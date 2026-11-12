-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Organizational units and the history of their codes and names.

-- ── dependencias ──────────────────────────────────────────────────────────────

CREATE TABLE dependencias (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    parent_id uuid,
    codigo integer NOT NULL,
    sigla character varying(20) NOT NULL,
    nombre text NOT NULL,
    depe_rad_tp2 boolean DEFAULT false NOT NULL,
    activo boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_dependencias_nombre_len CHECK ((char_length(nombre) <= 100)),
    CONSTRAINT dependencias_codigo_key UNIQUE (codigo),
    CONSTRAINT dependencias_pkey PRIMARY KEY (id),
    CONSTRAINT dependencias_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES dependencias(id) ON DELETE RESTRICT
);

CREATE INDEX ix_dependencias_activo ON dependencias USING btree (activo);
CREATE INDEX ix_dependencias_parent ON dependencias USING btree (parent_id);

-- ── dependencia_codigo_retirado ───────────────────────────────────────────────

CREATE TABLE dependencia_codigo_retirado (
    codigo integer NOT NULL,
    nombre text NOT NULL,
    retirado_por uuid,
    retirado_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT dependencia_codigo_retirado_pkey PRIMARY KEY (codigo)
);

-- ── dependencia_nombre_historico ──────────────────────────────────────────────

CREATE TABLE dependencia_nombre_historico (
    id bigint NOT NULL,
    codigo integer NOT NULL,
    nombre text NOT NULL,
    origen character varying(30) NOT NULL,
    motivo text,
    registrado_por uuid,
    at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT dependencia_nombre_historico_origen_check CHECK ((origen IN ('renombre', 'mapeo_manual'))),
    CONSTRAINT dependencia_nombre_historico_pkey PRIMARY KEY (id)
);

ALTER TABLE dependencia_nombre_historico ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME dependencia_nombre_historico_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE INDEX ix_dependencia_nombre_historico_nombre ON dependencia_nombre_historico USING btree (nombre);

-- ── Functions ─────────────────────────────────────────────────────────────────

CREATE FUNCTION reject_dependencia_ciclo() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path FROM CURRENT
    AS $$
BEGIN
    IF NEW.parent_id IS NULL THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.parent_id IS NOT DISTINCT FROM OLD.parent_id THEN
            RETURN NEW;
        END IF;
    END IF;

    -- Serializa los cambios de forma del árbol del schema: dos transacciones que
    -- cierran el ciclo cada una por su lado no se ven hasta confirmar.
    PERFORM pg_advisory_xact_lock(hashtext(current_schema() || ':dependencias_arbol'));

    IF NEW.parent_id = NEW.id THEN
        RAISE EXCEPTION 'dependencia_ciclo: una dependencia no puede ser su propio padre'
            USING ERRCODE = 'check_violation';
    END IF;

    IF EXISTS (
        WITH RECURSIVE arriba AS (
            SELECT d.id, d.parent_id FROM dependencias d WHERE d.id = NEW.parent_id
            UNION
            SELECT d.id, d.parent_id FROM dependencias d JOIN arriba a ON d.id = a.parent_id
        )
        SELECT 1 FROM arriba WHERE id = NEW.id
    ) THEN
        RAISE EXCEPTION 'dependencia_ciclo: el nuevo padre es descendiente de la dependencia (cerraria un ciclo en el organigrama)'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION reject_dependencia_codigo_change() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path FROM CURRENT
    AS $$
BEGIN
    IF NEW.codigo IS DISTINCT FROM OLD.codigo THEN
        RAISE EXCEPTION 'dependencia_codigo_inmutable: el código de una dependencia no se puede cambiar (% -> %)',
            OLD.codigo, NEW.codigo
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION reject_dependencia_codigo_retirado_mutation() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path FROM CURRENT
    AS $$
BEGIN
    RAISE EXCEPTION 'dependencia_codigo_retirado es de solo inserción (% bloqueado)', TG_OP
        USING ERRCODE = 'check_violation';
END
$$;

CREATE FUNCTION reject_dependencia_codigo_reutilizado() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path FROM CURRENT
    AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM dependencia_codigo_retirado r WHERE r.codigo = NEW.codigo)
       OR (EXISTS (SELECT 1 FROM dependencia_nombre_historico h WHERE h.codigo = NEW.codigo)
           AND NOT EXISTS (SELECT 1 FROM dependencias d WHERE d.codigo = NEW.codigo)) THEN
        RAISE EXCEPTION 'dependencia_codigo_retirado: el código % perteneció a una dependencia eliminada y no se reutiliza', NEW.codigo
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION reject_dependencia_nombre_historico_mutation() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path FROM CURRENT
    AS $$
BEGIN
    RAISE EXCEPTION 'dependencia_nombre_historico es de solo inserción (% bloqueado)', TG_OP
        USING ERRCODE = 'check_violation';
END
$$;

CREATE FUNCTION resolver_codigo_dependencia(p_nombre text, p_historico boolean) RETURNS TABLE(codigo integer, candidatos integer)
    LANGUAGE sql STABLE
    SET search_path FROM CURRENT
    AS $$
    WITH mapeo AS (
        SELECT DISTINCT h.codigo AS cod FROM dependencia_nombre_historico h
         WHERE p_historico AND h.origen = 'mapeo_manual' AND h.nombre = p_nombre
    ),
    c AS (
        SELECT m.cod FROM mapeo m
        UNION
        SELECT d.codigo FROM dependencias d
         WHERE NOT EXISTS (SELECT 1 FROM mapeo)
           AND d.nombre = p_nombre AND (p_historico OR d.activo)
        UNION
        SELECT h.codigo FROM dependencia_nombre_historico h
         WHERE p_historico AND NOT EXISTS (SELECT 1 FROM mapeo)
           AND h.origen = 'renombre' AND h.nombre = p_nombre
    )
    SELECT CASE WHEN count(*) = 1 THEN min(c.cod) END, count(*)::int FROM c
$$;

-- ── Triggers ──────────────────────────────────────────────────────────────────

CREATE TRIGGER trg_dependencias_codigo_inmutable BEFORE UPDATE OF codigo ON dependencias FOR EACH ROW EXECUTE FUNCTION reject_dependencia_codigo_change();
CREATE TRIGGER trg_dependencias_codigo_no_reutilizable BEFORE INSERT ON dependencias FOR EACH ROW EXECUTE FUNCTION reject_dependencia_codigo_reutilizado();
CREATE TRIGGER trg_dependencias_sin_ciclos BEFORE INSERT OR UPDATE OF parent_id ON dependencias FOR EACH ROW EXECUTE FUNCTION reject_dependencia_ciclo();

CREATE TRIGGER trg_dependencia_codigo_retirado_solo_insercion BEFORE DELETE OR UPDATE ON dependencia_codigo_retirado FOR EACH ROW EXECUTE FUNCTION reject_dependencia_codigo_retirado_mutation();

CREATE TRIGGER trg_dependencia_nombre_historico_solo_insercion BEFORE DELETE OR UPDATE ON dependencia_nombre_historico FOR EACH ROW EXECUTE FUNCTION reject_dependencia_nombre_historico_mutation();
