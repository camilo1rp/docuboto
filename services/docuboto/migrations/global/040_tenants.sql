-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- The registry of tenants. Each one owns a `tenant_<slug>` schema.

CREATE TABLE public.tenants (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    slug character varying(100) NOT NULL,
    name text NOT NULL,
    code character varying(20) NOT NULL,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    provisioning_status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    asistente_habilitado boolean DEFAULT false NOT NULL,
    intelligence_provider text DEFAULT 'local'::text NOT NULL,
    intelligence_levels smallint[] DEFAULT '{1}'::smallint[] NOT NULL,
    intelligence_send_metadata boolean DEFAULT false NOT NULL,
    CONSTRAINT tenants_intelligence_levels_chk CHECK ((intelligence_levels <@ ARRAY[(1)::smallint, (2)::smallint, (3)::smallint])),
    CONSTRAINT tenants_intelligence_provider_chk CHECK ((intelligence_provider = ANY (ARRAY['local'::text, 'ppi'::text]))),
    CONSTRAINT tenants_slug_forma_chk CHECK (((slug)::text ~ '^[a-z0-9][a-z0-9-]{1,53}$'::text)),
    CONSTRAINT tenants_pkey PRIMARY KEY (id),
    CONSTRAINT tenants_slug_key UNIQUE (slug),
    CONSTRAINT tenants_code_key UNIQUE (code)
);

CREATE INDEX ix_tenants_code ON public.tenants USING btree (code);
CREATE INDEX ix_tenants_created_at ON public.tenants USING btree (created_at);
CREATE INDEX ix_tenants_slug ON public.tenants USING btree (slug);
CREATE INDEX ix_tenants_status ON public.tenants USING btree (status);
