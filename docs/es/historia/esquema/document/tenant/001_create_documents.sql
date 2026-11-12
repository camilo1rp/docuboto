-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (ADR-012): tablas de radicación por institución.
--
-- El runner aplica este archivo dentro de CADA schema tenant_{slug}
-- (SET LOCAL search_path), nunca en public. Así `tracking_sequences` es
-- por-tenant: la numeración no se comparte entre instituciones.
--
-- El schema public sólo retiene el registro de tenants (tenant-service).

CREATE TABLE IF NOT EXISTS tracking_sequences (
    year        INTEGER     NOT NULL,
    doc_type    VARCHAR(1)  NOT NULL,  -- 'E', 'S', 'I'
    last_seq    INTEGER     NOT NULL DEFAULT 0,
    PRIMARY KEY (year, doc_type)
);

CREATE TABLE IF NOT EXISTS radicados (
    id              UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    tracking_number VARCHAR(50) NOT NULL UNIQUE,
    doc_type        VARCHAR(1)  NOT NULL,
    year            INTEGER     NOT NULL,
    sequence        INTEGER     NOT NULL,

    -- Document classification
    subject         TEXT        NOT NULL,
    doc_class       VARCHAR(100),

    -- Origin and destination
    origin_dept     VARCHAR(100),
    dest_dept       VARCHAR(100),
    sender_name     VARCHAR(255),
    sender_entity   VARCHAR(255),

    -- Lifecycle status
    status          VARCHAR(20) NOT NULL DEFAULT 'registered',

    -- Metadata
    pages           INTEGER     NOT NULL DEFAULT 0,
    observations    TEXT,
    registered_by   UUID,
    registered_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Denormalized tenant code for tracking number reconstruction
    tenant_code     VARCHAR(20) NOT NULL
);

CREATE TABLE IF NOT EXISTS anexos (
    id          UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    radicado_id UUID        NOT NULL REFERENCES radicados(id) ON DELETE CASCADE,
    -- file_id is the UUID returned by storage-service; no FK across services
    file_id     UUID        NOT NULL,
    filename    VARCHAR(255) NOT NULL,
    file_size   BIGINT,
    mime_type   VARCHAR(100),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_radicados_tracking_number ON radicados (tracking_number);
CREATE INDEX IF NOT EXISTS ix_radicados_doc_type        ON radicados (doc_type);
CREATE INDEX IF NOT EXISTS ix_radicados_status          ON radicados (status);
CREATE INDEX IF NOT EXISTS ix_radicados_registered_at   ON radicados (registered_at);
CREATE INDEX IF NOT EXISTS ix_anexos_radicado_id        ON anexos (radicado_id);
