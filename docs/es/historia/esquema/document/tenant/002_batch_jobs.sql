-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Batch API job tracking schema
-- Allows monitoring progress of bulk document/expediente operations

CREATE TABLE IF NOT EXISTS batch_jobs (
    id              UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    job_type        VARCHAR(50) NOT NULL, -- 'documents', 'expedientes'
    status          VARCHAR(20) NOT NULL DEFAULT 'pending', -- pending, processing, completed, failed
    created_by      UUID        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    total_items     INTEGER     NOT NULL,
    processed_items INTEGER     NOT NULL DEFAULT 0,
    failed_items    INTEGER     NOT NULL DEFAULT 0,
    error_message   TEXT
);

CREATE INDEX IF NOT EXISTS ix_batch_jobs_status ON batch_jobs (status);
CREATE INDEX IF NOT EXISTS ix_batch_jobs_created_by ON batch_jobs (created_by);
CREATE INDEX IF NOT EXISTS ix_batch_jobs_created_at ON batch_jobs (created_at DESC);

CREATE TABLE IF NOT EXISTS batch_job_items (
    id              UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    job_id          UUID        NOT NULL REFERENCES batch_jobs(id) ON DELETE CASCADE,
    item_index      INTEGER     NOT NULL, -- Position in original batch
    payload         JSONB       NOT NULL, -- Original request payload
    result_id       UUID, -- ID of created resource (radicado_id or expediente_id)
    status          VARCHAR(20) NOT NULL DEFAULT 'pending', -- pending, success, failed
    error_message   TEXT,
    processed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_batch_items_job_id ON batch_job_items (job_id);
CREATE INDEX IF NOT EXISTS ix_batch_items_status ON batch_job_items (status);
CREATE INDEX IF NOT EXISTS ix_batch_items_result_id ON batch_job_items (result_id);
