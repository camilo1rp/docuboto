-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E10 RF-PRE-05/07, ISO 14721 OAIS): paquete AIP.
--
-- AIP (Archival Information Package): empaquetado BagIt + manifiesto PREMIS del
-- expediente, con la huella de cada documento y la del propio AIP. El WORM real
-- (MinIO Object Lock) y la validación PDF/A (veraPDF) requieren infraestructura y
-- se abordan aparte; aquí se construyen y persisten los artefactos AIP/BagIt/PREMIS.

CREATE TABLE IF NOT EXISTS aip (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    expediente_id  TEXT NOT NULL,
    bagit          TEXT NOT NULL,        -- bagit.txt + manifest-sha256 + bag-info
    premis_xml     TEXT NOT NULL,        -- manifiesto PREMIS (objetos + evento ingesta)
    aip_sha256     CHAR(64) NOT NULL,    -- huella del propio AIP
    indice_sha256  CHAR(64),             -- huella del índice firmado incluido
    num_documentos INTEGER NOT NULL,
    estado         VARCHAR(12) NOT NULL DEFAULT 'empaquetado',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_aip_expediente ON aip (expediente_id, created_at DESC);
