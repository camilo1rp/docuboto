-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E03, RF-MET-07): catálogo de tipos documentales.
--
-- Tercer nivel de la TRD: el tipo documental cuelga de una serie/subserie, se
-- marca obligatorio u opcional, y referencia (por nombre, acoplamiento débil F1)
-- la plantilla de metadatos asociada en document-service. La FK dura a la matriz
-- TRD se consolida al cerrar E04.

CREATE TABLE IF NOT EXISTS tipos_documentales (
    id                UUID         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    trd_serie_id      UUID,                       -- serie/subserie (nullable en F1)
    code              VARCHAR(50)  NOT NULL,
    nombre            TEXT         NOT NULL,
    obligatorio       BOOLEAN      NOT NULL DEFAULT false,
    metadata_template TEXT,                        -- tipo_documental de document-service
    activo            BOOLEAN      NOT NULL DEFAULT true,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (trd_serie_id, code)
);

CREATE INDEX IF NOT EXISTS ix_tipos_documentales_serie
    ON tipos_documentales (trd_serie_id);
