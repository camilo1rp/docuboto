-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Batch jobs.

-- ── batch_jobs ────────────────────────────────────────────────────────────────

CREATE TABLE batch_jobs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_type character varying(50) NOT NULL,
    status character varying(40) DEFAULT 'pending'::character varying NOT NULL,
    created_by uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    total_items integer NOT NULL,
    processed_items integer DEFAULT 0 NOT NULL,
    failed_items integer DEFAULT 0 NOT NULL,
    error_message text,
    reanudaciones integer DEFAULT 0 NOT NULL,
    reprocesado_por uuid,
    reprocesado_at timestamp with time zone,
    CONSTRAINT batch_jobs_pkey PRIMARY KEY (id)
);

CREATE INDEX ix_archive_batch_jobs_created_at ON batch_jobs USING btree (created_at DESC);
CREATE INDEX ix_archive_batch_jobs_status ON batch_jobs USING btree (status);
CREATE INDEX ix_batch_jobs_created_at ON batch_jobs USING btree (created_at DESC);
CREATE INDEX ix_batch_jobs_created_by ON batch_jobs USING btree (created_by);
CREATE INDEX ix_batch_jobs_status ON batch_jobs USING btree (status);

-- ── batch_job_items ───────────────────────────────────────────────────────────

CREATE TABLE batch_job_items (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_id uuid NOT NULL,
    item_index integer NOT NULL,
    payload jsonb NOT NULL,
    result_id uuid,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    error_message text,
    processed_at timestamp with time zone,
    intentos integer DEFAULT 0 NOT NULL,
    error_tipo character varying(100),
    CONSTRAINT batch_job_items_pkey PRIMARY KEY (id),
    CONSTRAINT batch_job_items_job_id_fkey FOREIGN KEY (job_id) REFERENCES batch_jobs(id) ON DELETE CASCADE
);

COMMENT ON COLUMN batch_job_items.intentos IS 'Veces que se intentó procesar el ítem (el primer intento cuenta 1).';

COMMENT ON COLUMN batch_job_items.error_tipo IS 'Clase de la excepción del último fallo (el motivo legible va en error_message).';

CREATE INDEX ix_archive_batch_items_job_id ON batch_job_items USING btree (job_id);
CREATE INDEX ix_archive_batch_items_status ON batch_job_items USING btree (status);
CREATE INDEX ix_batch_items_job_id ON batch_job_items USING btree (job_id);
CREATE INDEX ix_batch_items_result_id ON batch_job_items USING btree (result_id);
CREATE INDEX ix_batch_items_status ON batch_job_items USING btree (status);
