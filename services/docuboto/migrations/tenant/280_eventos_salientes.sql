-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Outbox: outgoing events and work done outside the request.

-- ── eventos_salientes ─────────────────────────────────────────────────────────

CREATE TABLE eventos_salientes (
    id bigint NOT NULL,
    event_id text NOT NULL,
    stream text NOT NULL,
    tipo text NOT NULL,
    tenant_slug text NOT NULL,
    actor text,
    dedup_key text,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    creado_at timestamp with time zone DEFAULT now() NOT NULL,
    enviado_at timestamp with time zone,
    intentos integer DEFAULT 0 NOT NULL,
    ultimo_error text,
    CONSTRAINT eventos_salientes_event_id_key UNIQUE (event_id),
    CONSTRAINT eventos_salientes_pkey PRIMARY KEY (id)
);

ALTER TABLE eventos_salientes ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME eventos_salientes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

COMMENT ON TABLE eventos_salientes IS 'Outbox transaccional (AREA-09-05): el evento nace en la misma tx que el cambio.';

CREATE INDEX ix_eventos_salientes_pendientes ON eventos_salientes USING btree (id) WHERE (enviado_at IS NULL);

-- ── trabajo_fuera ─────────────────────────────────────────────────────────────

CREATE TABLE trabajo_fuera (
    id bigint NOT NULL,
    event_id text NOT NULL,
    tipo text NOT NULL,
    cola text NOT NULL,
    tenant_slug text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    creado_at timestamp with time zone DEFAULT now() NOT NULL,
    reclamado_at timestamp with time zone,
    reclamado_por text,
    hecho_at timestamp with time zone,
    intentos integer DEFAULT 0 NOT NULL,
    ultimo_error text,
    CONSTRAINT trabajo_fuera_cola_check CHECK ((cola = ANY (ARRAY['app'::text, 'worker'::text]))),
    CONSTRAINT trabajo_fuera_event_id_key UNIQUE (event_id),
    CONSTRAINT trabajo_fuera_pkey PRIMARY KEY (id)
);

ALTER TABLE trabajo_fuera ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME trabajo_fuera_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

COMMENT ON TABLE trabajo_fuera IS 'Outbox of outside work (Docuboto 2.0): app vs worker claim by type.';

CREATE INDEX ix_trabajo_fuera_pendientes ON trabajo_fuera USING btree (cola, id) WHERE (hecho_at IS NULL);
