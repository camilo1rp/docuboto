-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Archival information packages.

-- ── aip ───────────────────────────────────────────────────────────────────────

CREATE TABLE aip (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    expediente_id text NOT NULL,
    bagit text,
    premis_xml text NOT NULL,
    aip_sha256 character(64) NOT NULL,
    indice_sha256 character(64),
    num_documentos integer NOT NULL,
    estado character varying(12) DEFAULT 'empaquetado'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    bucket text,
    object_key text,
    aip_bytes bigint,
    formato text DEFAULT 'application/zip'::text NOT NULL,
    retain_until timestamp with time zone,
    retention_mode text,
    legal_hold boolean DEFAULT false NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    sustituye_a uuid,
    motivo character varying(16) DEFAULT 'ingesta'::character varying NOT NULL,
    CONSTRAINT aip_motivo_check CHECK ((motivo IN ('ingesta', 'renovacion_lta'))),
    CONSTRAINT aip_version_check CHECK ((version >= 1)),
    CONSTRAINT aip_version_coherente CHECK ((((version = 1) = (sustituye_a IS NULL)) AND (((motivo)::text = 'ingesta'::text) = (sustituye_a IS NULL)))),
    CONSTRAINT aip_pkey PRIMARY KEY (id),
    CONSTRAINT aip_sustituye_a_fkey FOREIGN KEY (sustituye_a) REFERENCES aip(id)
);

CREATE INDEX ix_aip_expediente ON aip USING btree (expediente_id, created_at DESC);
CREATE UNIQUE INDEX ux_aip_expediente_version ON aip USING btree (expediente_id, version);
CREATE UNIQUE INDEX ux_aip_sustituye_a ON aip USING btree (sustituye_a) WHERE (sustituye_a IS NOT NULL);
