-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E03, ADR-007): metadatos flexibles del expediente.
--
-- Igual que en el radicado: los metadatos variables del expediente se guardan
-- en `metadata JSONB`, validados en la capa de servicio contra el JSON Schema
-- de la plantilla activa de su serie TRD. Índice GIN para filtrar por campo.
--
-- F1 (acoplamiento débil, R4 del spec E03): la plantilla se identifica por
-- `trd_serie_id` (la serie del expediente) hasta que E04 consolide la TRD.

CREATE TABLE IF NOT EXISTS expediente_metadata_templates (
    id            UUID         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    trd_serie_id  UUID         NOT NULL,
    version       INTEGER      NOT NULL DEFAULT 1,
    json_schema   JSONB        NOT NULL,
    activo        BOOLEAN      NOT NULL DEFAULT true,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (trd_serie_id, version)
);

-- Una sola plantilla activa por serie.
CREATE UNIQUE INDEX IF NOT EXISTS ux_exp_metadata_templates_activa
    ON expediente_metadata_templates (trd_serie_id)
    WHERE activo;

ALTER TABLE expedientes
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE expedientes
    ADD COLUMN IF NOT EXISTS metadata_template_id UUID;

CREATE INDEX IF NOT EXISTS ix_expedientes_metadata_gin
    ON expedientes USING GIN (metadata);
