-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Stored files.

-- ── files ─────────────────────────────────────────────────────────────────────

CREATE TABLE files (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    filename character varying(255) NOT NULL,
    mime_type character varying(100),
    file_size bigint NOT NULL,
    sha256 character varying(64) NOT NULL,
    bucket character varying(255) NOT NULL,
    object_key character varying(500) NOT NULL,
    uploaded_by uuid,
    uploaded_at timestamp with time zone DEFAULT now() NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    is_current boolean DEFAULT true NOT NULL,
    root_id uuid,
    replaces_id uuid,
    deleted_at timestamp with time zone,
    deleted_by uuid,
    pdfa_status text,
    pdfa_profile text,
    pdfa_checked_at timestamp with time zone,
    dedup_hit_at timestamp with time zone,
    CONSTRAINT files_pdfa_status_check CHECK (((pdfa_status IS NULL) OR (pdfa_status = ANY (ARRAY['conformant'::text, 'non_conformant'::text, 'not_evaluated'::text, 'pendiente'::text])))),
    CONSTRAINT files_pkey PRIMARY KEY (id)
);

COMMENT ON COLUMN files.pdfa_status IS 'Veredicto PDF/A durable del objeto (E10 C2b follow-up, ingesta E07). NULL = nunca evaluado (gate apagado / no-PDF / archivo previo al incremento). "pendiente" = encolado para el worker asíncrono best-effort (modo advisory). Terminal: conformant|non_conformant|not_evaluated -- reusado por AipService.empaquetar_aip si el perfil coincide.';

COMMENT ON COLUMN files.pdfa_profile IS 'Perfil PDF/A (p. ej. "2b") contra el que se evaluó pdfa_status -- permite al empaquetado del AIP detectar divergencia de perfil y re-validar solo si difiere.';

COMMENT ON COLUMN files.pdfa_checked_at IS 'Instante en que pdfa_status quedó en un estado TERMINAL. NULL mientras esté en NULL o "pendiente".';

COMMENT ON COLUMN files.dedup_hit_at IS 'Última vez que un upload devolvió este objeto por deduplicación SHA-256. La purga de huérfanos nunca borra un objeto con la marca.';

CREATE INDEX ix_files_active ON files USING btree (id) WHERE (deleted_at IS NULL);
CREATE INDEX ix_files_current ON files USING btree (root_id) WHERE is_current;
CREATE INDEX ix_files_root_version ON files USING btree (root_id, version);
CREATE INDEX ix_files_sha256 ON files USING btree (sha256);
CREATE INDEX ix_files_uploaded_at ON files USING btree (uploaded_at);
