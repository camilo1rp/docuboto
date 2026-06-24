-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E03, RF-MET-03): catálogo de elementos de metadato.
--
-- Definiciones reutilizables de campos de metadato con sus atributos (R.3.1/R.3.5):
-- clave técnica, etiqueta, tipo de dato, longitud, ocurrencias, modificable,
-- valor por defecto, opciones (select), orden, searchable y gancho de mapeo a
-- normas de descripción (Dublin Core/ISAD, RF-MET-09). Las plantillas
-- (metadata_templates) referencian estas claves en su JSON Schema.

CREATE TABLE IF NOT EXISTS metadata_elements (
    id                 UUID         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    clave              TEXT         NOT NULL UNIQUE,
    etiqueta           TEXT         NOT NULL,
    tipo_dato          VARCHAR(20)  NOT NULL,  -- text|number|date|boolean|select
    longitud           INTEGER,                -- p. ej. 50/100/200/500 para texto
    ocurrencia_min     INTEGER      NOT NULL DEFAULT 0,
    ocurrencia_max     INTEGER,                -- NULL = ilimitado
    modificable        BOOLEAN      NOT NULL DEFAULT true,
    valor_default      JSONB,
    opciones           JSONB,                  -- valores admitidos si tipo_dato='select'
    orden              INTEGER      NOT NULL DEFAULT 0,
    searchable         BOOLEAN      NOT NULL DEFAULT false,
    mapeo_dublin_core  TEXT,                   -- gancho RF-MET-09 (Dublin Core/ISAD/EAD)
    activo             BOOLEAN      NOT NULL DEFAULT true,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_metadata_elements_activo
    ON metadata_elements (activo, orden);
