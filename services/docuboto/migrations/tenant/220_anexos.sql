-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Attachments and their signatures.

-- ── anexos ────────────────────────────────────────────────────────────────────

CREATE TABLE anexos (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    radicado_id uuid NOT NULL,
    file_id uuid NOT NULL,
    filename character varying(255) NOT NULL,
    file_size bigint,
    mime_type character varying(100),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    tipo_anexo character varying(50),
    es_principal boolean DEFAULT false NOT NULL,
    folios integer,
    publicable boolean DEFAULT false NOT NULL,
    CONSTRAINT anexos_pkey PRIMARY KEY (id),
    CONSTRAINT anexos_radicado_id_fkey FOREIGN KEY (radicado_id) REFERENCES radicados(id) ON DELETE CASCADE
);

COMMENT ON COLUMN anexos.publicable IS 'Acto positivo de difusión POR ANEXO (Ley 1712/2014 art. 12). FALSE por defecto. CMIS solo sirve anexos con publicable = TRUE de radicados publicados.';

CREATE INDEX ix_anexos_radicado_id ON anexos USING btree (radicado_id);

-- ── signatures ────────────────────────────────────────────────────────────────

CREATE TABLE signatures (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    radicado_id uuid NOT NULL,
    anexo_id uuid,
    signer_id uuid,
    signer_name character varying(255),
    content_hash character varying(64) NOT NULL,
    reason text,
    signed_at timestamp with time zone DEFAULT now() NOT NULL,
    origen character varying(30) DEFAULT 'servidor'::character varying NOT NULL,
    CONSTRAINT ck_signatures_origen CHECK ((origen IN ('servidor', 'cliente_no_verificado'))),
    CONSTRAINT signatures_pkey PRIMARY KEY (id)
);

COMMENT ON COLUMN signatures.origen IS 'servidor = huella resuelta por el servidor desde storage; cliente_no_verificado = huella declarada por el cliente (heredada), excluida de verify.';

CREATE INDEX ix_signatures_radicado ON signatures USING btree (radicado_id);

-- ── import_binario_huerfano ───────────────────────────────────────────────────

CREATE TABLE import_binario_huerfano (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    import_uuid uuid NOT NULL,
    file_id uuid NOT NULL,
    sha256 character varying(64),
    actor uuid,
    subido_desde timestamp with time zone NOT NULL,
    estado character varying(20) DEFAULT 'pendiente'::character varying NOT NULL,
    intentos integer DEFAULT 0 NOT NULL,
    ultimo_error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    resuelto_at timestamp with time zone,
    CONSTRAINT ck_import_binario_huerfano_estado CHECK ((estado IN ('pendiente', 'purgado', 'conservado', 'protegido', 'agotado'))),
    CONSTRAINT import_binario_huerfano_pkey PRIMARY KEY (id),
    CONSTRAINT ux_import_binario_huerfano UNIQUE (import_uuid, file_id)
);

CREATE INDEX ix_import_binario_huerfano_pendiente ON import_binario_huerfano USING btree (created_at) WHERE ((estado)::text = 'pendiente'::text);
