-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E03, ADR-007): metadatos flexibles por tipo documental.
--
-- Los metadatos variables del radicado se almacenan en `metadata JSONB`,
-- validados en la capa de servicio contra el JSON Schema de la plantilla activa
-- del tipo documental (no EAV, sin DDL en runtime — ver ADR-007). Índice GIN
-- para filtrar/buscar por campo de metadato.
--
-- F1 (acoplamiento débil, R4 del spec E03): la plantilla se identifica por
-- `tipo_documental` TEXT (= radicados.doc_class) hasta que E04 consolide el
-- catálogo de tipos documentales con FK.

CREATE TABLE IF NOT EXISTS metadata_templates (
    id               UUID         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    tipo_documental  TEXT         NOT NULL,
    version          INTEGER      NOT NULL DEFAULT 1,
    json_schema      JSONB        NOT NULL,
    activo           BOOLEAN      NOT NULL DEFAULT true,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (tipo_documental, version)
);

-- Una sola plantilla activa por tipo documental.
CREATE UNIQUE INDEX IF NOT EXISTS ux_metadata_templates_activa
    ON metadata_templates (tipo_documental)
    WHERE activo;

ALTER TABLE radicados
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE radicados
    ADD COLUMN IF NOT EXISTS metadata_template_id UUID;

CREATE INDEX IF NOT EXISTS ix_radicados_metadata_gin
    ON radicados USING GIN (metadata);
