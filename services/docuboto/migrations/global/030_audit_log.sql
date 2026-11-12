-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- The audit log (ADR-008): one hash-chained, append-only table for every tenant,
-- partitioned by month. Four layers keep it append-only: REVOKE from PUBLIC and
-- from the application role, a row trigger, statement triggers (TRUNCATE has no
-- row trigger and partitions do not inherit statement triggers), and a CHECK on
-- the shape of the hash.

-- ── audit_log ─────────────────────────────────────────────────────────────────

CREATE TABLE public.audit_log (
    id bigint NOT NULL,
    tenant_slug text NOT NULL,
    ts timestamp with time zone DEFAULT now() NOT NULL,
    service text NOT NULL,
    actor text,
    canal text DEFAULT 'api'::text NOT NULL,
    action text NOT NULL,
    object_type text NOT NULL,
    object_ref text,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    prev_hash text,
    hash text NOT NULL,
    CONSTRAINT chk_audit_log_canal CHECK ((canal = ANY (ARRAY['api'::text, 'ui'::text, 'mcp'::text, 'system'::text]))),
    CONSTRAINT chk_audit_log_hash_forma CHECK (((hash ~ '^[0-9a-f]{64}$'::text) AND ((prev_hash IS NULL) OR (prev_hash ~ '^[0-9a-f]{64}$'::text)))),
    CONSTRAINT audit_log_pkey PRIMARY KEY (id, ts)
)
PARTITION BY RANGE (ts);

ALTER TABLE public.audit_log ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.audit_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

COMMENT ON CONSTRAINT chk_audit_log_hash_forma ON public.audit_log IS 'Forma SHA-256 (64 hex) de hash/prev_hash. Cuarta capa de la defensa del registro: las tres anteriores impiden UPDATE/DELETE, esta impide INSERTAR un asiento que no venga de orpycamcp_common.audit.append.';

CREATE INDEX ix_audit_object ON public.audit_log USING btree (object_type, object_ref);
CREATE INDEX ix_audit_tenant_ts ON public.audit_log USING btree (tenant_slug, ts);

CREATE TABLE public.audit_log_default PARTITION OF public.audit_log DEFAULT;

-- ── Guards ────────────────────────────────────────────────────────────────────

CREATE FUNCTION public.reject_audit_log_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'public.audit_log is append-only (Ley 594/2000, ADR-008)';
END;
$$;

CREATE FUNCTION public.reject_audit_log_mutation_d03() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION
        'UPDATE/DELETE on public.audit_log is prohibited '
        '(ADR-008, Ley 594/2000, D-03): table is append-only';
END;
$$;

CREATE FUNCTION public.reject_audit_log_truncate() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION
        'TRUNCATE on public.audit_log is prohibited (ADR-008, Ley 594/2000, D-03)';
END;
$$;

REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public.audit_log FROM PUBLIC, orpycamcp_app;
REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public.audit_log_default FROM PUBLIC, orpycamcp_app;

CREATE TRIGGER trg_audit_log_immutable BEFORE DELETE OR UPDATE ON public.audit_log FOR EACH ROW EXECUTE FUNCTION public.reject_audit_log_mutation();
CREATE TRIGGER trg_audit_log_no_mutation BEFORE DELETE OR UPDATE ON public.audit_log FOR EACH STATEMENT EXECUTE FUNCTION public.reject_audit_log_mutation_d03();
CREATE TRIGGER trg_audit_log_no_truncate BEFORE TRUNCATE ON public.audit_log FOR EACH STATEMENT EXECUTE FUNCTION public.reject_audit_log_truncate();
CREATE TRIGGER trg_audit_log_default_no_mutation BEFORE DELETE OR UPDATE ON public.audit_log_default FOR EACH STATEMENT EXECUTE FUNCTION public.reject_audit_log_mutation_d03();
CREATE TRIGGER trg_audit_log_default_no_truncate BEFORE TRUNCATE ON public.audit_log_default FOR EACH STATEMENT EXECUTE FUNCTION public.reject_audit_log_truncate();

-- ── Monthly partitions ────────────────────────────────────────────────────────

CREATE FUNCTION public.harden_audit_partition(partition_name text) RETURNS void
    LANGUAGE plpgsql
    AS $_$
DECLARE
    _trig_trunc TEXT;
    _trig_mut   TEXT;
BEGIN
    IF partition_name !~ '^audit_log[a-z0-9_]*$' THEN
        RAISE EXCEPTION
            'harden_audit_partition: nombre de partición inválido: %. '
            'Debe empezar por audit_log y contener solo [a-z0-9_].', partition_name;
    END IF;

    EXECUTE format(
        'REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public.%I FROM PUBLIC', partition_name);

    -- El rol de aplicación recibe UPDATE/DELETE por los privilegios por omisión
    -- (migración 008). Sobre la auditoría se retiran: solo se AÑADE.
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'orpycamcp_app') THEN
        EXECUTE format(
            'REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public.%I FROM orpycamcp_app',
            partition_name);
    END IF;

    _trig_trunc := 'trg_' || partition_name || '_no_truncate';
    _trig_mut   := 'trg_' || partition_name || '_no_mutation';

    EXECUTE format('DROP TRIGGER IF EXISTS %I ON public.%I', _trig_trunc, partition_name);
    EXECUTE format(
        'CREATE TRIGGER %I BEFORE TRUNCATE ON public.%I '
        'FOR EACH STATEMENT EXECUTE FUNCTION public.reject_audit_log_mutation_d03()',
        _trig_trunc, partition_name);

    EXECUTE format('DROP TRIGGER IF EXISTS %I ON public.%I', _trig_mut, partition_name);
    EXECUTE format(
        'CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON public.%I '
        'FOR EACH STATEMENT EXECUTE FUNCTION public.reject_audit_log_mutation_d03()',
        _trig_mut, partition_name);
END;
$_$;

CREATE FUNCTION public.ensure_audit_partitions(meses_adelante integer DEFAULT 3) RETURNS TABLE(particion text, creada boolean)
    LANGUAGE plpgsql
    AS $$
DECLARE
    _max_default timestamptz;
    _inicio      date;
    _mes         date;
    _fin         date;
    _nombre      text;
BEGIN
    IF meses_adelante < 0 THEN
        RAISE EXCEPTION 'ensure_audit_partitions: meses_adelante no puede ser negativo';
    END IF;

    -- Primer mes seguro: el siguiente al último asiento que quedó en el default.
    -- Sin esto, el CREATE TABLE ... FOR VALUES fallaría con «updated partition
    -- constraint for default partition would be violated by some row», que es un
    -- mensaje correcto y completamente opaco a las 3 de la mañana.
    SELECT max(ts) INTO _max_default FROM public.audit_log_default;

    _inicio := date_trunc('month', now())::date;
    IF _max_default IS NOT NULL THEN
        _inicio := greatest(_inicio, (date_trunc('month', _max_default) + interval '1 month')::date);
    END IF;

    _mes := _inicio;
    WHILE _mes <= (date_trunc('month', now()) + (meses_adelante || ' months')::interval)::date LOOP
        _fin := (_mes + interval '1 month')::date;
        _nombre := 'audit_log_' || to_char(_mes, 'YYYY_MM');

        IF EXISTS (
            SELECT 1 FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = _nombre
        ) THEN
            particion := _nombre; creada := false; RETURN NEXT;
        ELSE
            EXECUTE format(
                'CREATE TABLE public.%I PARTITION OF public.audit_log '
                'FOR VALUES FROM (%L) TO (%L)', _nombre, _mes, _fin
            );
            -- Sin esto la partición nueva sería la única parte de `audit_log`
            -- que SÍ admite UPDATE/DELETE/TRUNCATE: los guards de 003/004 se
            -- aplican por tabla, y una partición recién creada no los hereda.
            -- Crear particiones sin endurecerlas abriría, mes a mes, un agujero
            -- en la inmutabilidad del registro.
            PERFORM public.harden_audit_partition(_nombre);
            particion := _nombre; creada := true; RETURN NEXT;
        END IF;

        _mes := _fin;
    END LOOP;
END;
$$;

COMMENT ON FUNCTION public.ensure_audit_partitions(meses_adelante integer) IS 'Crea y endurece las particiones mensuales de public.audit_log que falten hasta N meses por delante (ADR-008). Idempotente. No mueve el histórico de audit_log_default: arranca en el primer mes que no solape con él. Invocación operativa: scripts/audit_partitions.py (cron mensual).';

SELECT public.ensure_audit_partitions(3);

-- ── audit_chain_runs ──────────────────────────────────────────────────────────

CREATE TABLE public.audit_chain_runs (
    id bigserial NOT NULL,
    started_at timestamp with time zone NOT NULL,
    finished_at timestamp with time zone NOT NULL,
    alcance text NOT NULL,
    origen text NOT NULL,
    tenants integer NOT NULL,
    asientos bigint NOT NULL,
    roturas_nuevas integer NOT NULL,
    roturas_declaradas integer NOT NULL,
    retrocesos integer NOT NULL,
    anclas_alteradas integer NOT NULL,
    codigo_salida smallint NOT NULL,
    CONSTRAINT audit_chain_runs_alcance_check CHECK ((alcance = ANY (ARRAY['completo'::text, 'parcial'::text]))),
    CONSTRAINT audit_chain_runs_anclas_alteradas_check CHECK ((anclas_alteradas >= 0)),
    CONSTRAINT audit_chain_runs_asientos_check CHECK ((asientos >= 0)),
    CONSTRAINT audit_chain_runs_origen_check CHECK ((origen = ANY (ARRAY['backup'::text, 'restore'::text, 'cron'::text, 'manual'::text]))),
    CONSTRAINT audit_chain_runs_retrocesos_check CHECK ((retrocesos >= 0)),
    CONSTRAINT audit_chain_runs_roturas_declaradas_check CHECK ((roturas_declaradas >= 0)),
    CONSTRAINT audit_chain_runs_roturas_nuevas_check CHECK ((roturas_nuevas >= 0)),
    CONSTRAINT audit_chain_runs_tenants_check CHECK ((tenants >= 0)),
    CONSTRAINT audit_chain_runs_pkey PRIMARY KEY (id)
);

CREATE INDEX ix_audit_chain_runs_completo ON public.audit_chain_runs USING btree (id DESC) WHERE (alcance = 'completo'::text);

-- Runs of the chain verifier are appended, never rewritten.
REVOKE UPDATE, DELETE ON TABLE public.audit_chain_runs FROM orpycamcp_app;
