-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E10, F5, AGN 001/2024 Art. 4.3.2.6; ISO 14721 OAIS):
-- Plan de Preservación Digital (RF-PRE-01) + eventos de preservación (RF-PRE-07).
--
-- El plan es versionado APPEND-ONLY (vigente = MAX(version)). Los eventos son un
-- registro inmutable estilo PREMIS de toda intervención de preservación
-- (ingesta, fijación, migración, validación PDF/A, WORM). El empaquetado AIP/BagIt
-- (RF-PRE-05), el Object Lock/WORM real (RF-PRE-03) y la validación veraPDF
-- (RF-PRE-02) requieren infraestructura/librerías y se abordan aparte.

CREATE TABLE IF NOT EXISTS preservacion_plan (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version                  INTEGER NOT NULL,
    formatos_destino         TEXT[] NOT NULL DEFAULT '{}',   -- p. ej. PDF/A-2b, TIFF
    num_copias               INTEGER NOT NULL DEFAULT 1,
    periodicidad_fixity_dias INTEGER NOT NULL DEFAULT 180,
    politica_migracion       TEXT,
    contingencia             TEXT,
    creado_por               TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (version)
);

CREATE TABLE IF NOT EXISTS preservacion_evento (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    object_ref   TEXT NOT NULL,                 -- expediente_id / file_id / aip id
    tipo         VARCHAR(24) NOT NULL,          -- INGESTA|FIJACION|MIGRACION|VALIDACION_PDFA|WORM|REPLICA
    resultado    VARCHAR(8) NOT NULL DEFAULT 'ok' CHECK (resultado IN ('ok','fallo')),
    hash_before  CHAR(64),
    hash_after   CHAR(64),
    detalle      JSONB,
    agente       TEXT,
    ts           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_preservacion_evento_obj
    ON preservacion_evento (object_ref, ts);
