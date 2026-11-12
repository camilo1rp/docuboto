-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Metadata templates for filed records.

-- ── metadata_templates ────────────────────────────────────────────────────────

CREATE TABLE metadata_templates (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tipo_documental text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    json_schema jsonb NOT NULL,
    activo boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT metadata_templates_pkey PRIMARY KEY (id),
    CONSTRAINT metadata_templates_tipo_documental_version_key UNIQUE (tipo_documental, version)
);

CREATE UNIQUE INDEX ux_metadata_templates_activa ON metadata_templates USING btree (tipo_documental) WHERE activo;

-- ── metadata_elements ─────────────────────────────────────────────────────────

CREATE TABLE metadata_elements (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    clave text NOT NULL,
    etiqueta text NOT NULL,
    tipo_dato character varying(20) NOT NULL,
    longitud integer,
    ocurrencia_min integer DEFAULT 0 NOT NULL,
    ocurrencia_max integer,
    modificable boolean DEFAULT true NOT NULL,
    valor_default jsonb,
    opciones jsonb,
    orden integer DEFAULT 0 NOT NULL,
    searchable boolean DEFAULT false NOT NULL,
    mapeo_dublin_core text,
    activo boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT metadata_elements_clave_key UNIQUE (clave),
    CONSTRAINT metadata_elements_pkey PRIMARY KEY (id)
);

CREATE INDEX ix_metadata_elements_activo ON metadata_elements USING btree (activo, orden);
