-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Batch job tracking for archive-service (expedientes bulk creation)
-- Same pattern as document-service batch_jobs

CREATE TABLE IF NOT EXISTS batch_jobs (
    id              UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    job_type        VARCHAR(50) NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',
    created_by      UUID        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    total_items     INTEGER     NOT NULL,
    processed_items INTEGER     NOT NULL DEFAULT 0,
    failed_items    INTEGER     NOT NULL DEFAULT 0,
    error_message   TEXT
);

CREATE INDEX IF NOT EXISTS ix_archive_batch_jobs_status ON batch_jobs (status);
CREATE INDEX IF NOT EXISTS ix_archive_batch_jobs_created_at ON batch_jobs (created_at DESC);

CREATE TABLE IF NOT EXISTS batch_job_items (
    id              UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    job_id          UUID        NOT NULL REFERENCES batch_jobs(id) ON DELETE CASCADE,
    item_index      INTEGER     NOT NULL,
    payload         JSONB       NOT NULL,
    result_id       UUID,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',
    error_message   TEXT,
    processed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_archive_batch_items_job_id ON batch_job_items (job_id);
CREATE INDEX IF NOT EXISTS ix_archive_batch_items_status ON batch_job_items (status);
