-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Which release migrated this database (ADR-053 §3). The runner records its
-- own `ORPYCA_VERSION` (baked into the image at build time) after applying the
-- chain, so "what runs in this municipality" is a query:
--
--   SELECT version, applied_at FROM public.schema_version ORDER BY applied_at DESC LIMIT 1;
--
-- One row per version ever applied; rolling back to an earlier release moves
-- that row's `applied_at` forward again. The application only reads it.

CREATE TABLE public.schema_version (
    version text NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT schema_version_pkey PRIMARY KEY (version)
);

REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE public.schema_version FROM PUBLIC, orpycamcp_app;
