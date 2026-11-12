-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Creates the files table in the calling tenant's schema.
-- This migration is applied after SET search_path = "tenant_{slug}" (solo el tenant, ADR-002), public,
-- so all objects land in the correct schema without schema-qualifying.

CREATE TABLE IF NOT EXISTS files (
    id          UUID         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    filename    VARCHAR(255) NOT NULL,
    mime_type   VARCHAR(100),
    file_size   BIGINT       NOT NULL,
    sha256      VARCHAR(64)  NOT NULL,
    bucket      VARCHAR(255) NOT NULL,
    object_key  VARCHAR(500) NOT NULL,
    uploaded_by UUID,
    uploaded_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_files_sha256      ON files (sha256);
CREATE INDEX IF NOT EXISTS ix_files_uploaded_at ON files (uploaded_at);
