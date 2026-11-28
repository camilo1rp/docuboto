-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Event bus (ADR-049): events live in the tenant's schema, published in the
-- same transaction as the domain change; and work done outside the request.

-- ── eventos ───────────────────────────────────────────────────────────────────

CREATE TABLE eventos (
    seq bigint NOT NULL,
    stream text NOT NULL,
    event_id text NOT NULL,
    sobre jsonb NOT NULL,
    publicado_en timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT eventos_event_id_key UNIQUE (event_id),
    CONSTRAINT eventos_pkey PRIMARY KEY (seq)
);

ALTER TABLE eventos ALTER COLUMN seq ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME eventos_seq_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

COMMENT ON TABLE eventos IS 'Bus de eventos (ADR-049): el evento nace en la misma tx que el cambio. sobre = campos planos firmados (events.to_fields).';

CREATE INDEX ix_eventos_stream_seq ON eventos USING btree (stream, seq);
CREATE INDEX ix_eventos_publicado_en ON eventos USING btree (publicado_en);

-- ── evento_entregas ───────────────────────────────────────────────────────────

CREATE TABLE evento_entregas (
    group_name text NOT NULL,
    event_id text NOT NULL,
    seq bigint NOT NULL,
    reclamado_en timestamp with time zone DEFAULT now() NOT NULL,
    entregas smallint DEFAULT 1 NOT NULL,
    confirmado_en timestamp with time zone,
    CONSTRAINT evento_entregas_pkey PRIMARY KEY (group_name, event_id)
);

COMMENT ON TABLE evento_entregas IS 'Una fila por (grupo, evento) tomado: reclamarlo es insertarla, confirmarlo es fijar confirmado_en. Sin confirmar = pendiente de reintento o de dead-letter (ADR-049). No hay cursor por seq: una tx que commitea tarde con un seq menor quedaría saltada.';

CREATE INDEX ix_evento_entregas_pendientes ON evento_entregas USING btree (group_name, reclamado_en) WHERE (confirmado_en IS NULL);
CREATE INDEX ix_evento_entregas_event_id ON evento_entregas USING btree (event_id);

-- ── evento_ultimo_error ───────────────────────────────────────────────────────

CREATE TABLE evento_ultimo_error (
    group_name text NOT NULL,
    event_id text NOT NULL,
    mensaje text NOT NULL,
    ocurrido_en timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT evento_ultimo_error_pkey PRIMARY KEY (group_name, event_id)
);

COMMENT ON TABLE evento_ultimo_error IS 'Último error de cada entrega fallida (R-5b); se purga a las 24 h (ADR-049).';

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
