-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- C-6 (ADR-042): el AIP se VERSIONA, no se reescribe. Cuando el ArchiveTimeStamp del índice se
-- renueva (XAdES-LTA), la fase `run_once_aip_reempaquetado` pide a storage-service un AIP nuevo
-- cuyo único cambio es el índice embebido. `expediente_aip` sigue siendo UNA fila por expediente
-- (estado del empaquetado); la historia vive aquí.
--
-- Identidad de la versión: `indice_xml_sha256` (huella del XML del índice embebido). El UNIQUE
-- (expediente_id, indice_xml_sha256) hace idempotente el reempaquetado ante dos réplicas.
-- `expediente_aip.aip_worm_object_id` NO cambia: apunta a la PRIMERA versión (identidad de
-- retención); `aip_vigente_id` apunta a la última (la de consulta).
--
-- Append-only: sin UPDATE/DELETE sobre las versiones (trigger, mismo patrón que 059).
-- Sin backfill: un AIP v1 anterior a esta migración queda con `indice_xml_sha256 IS NULL` y se
-- trata como desconocido (se reempaqueta si la firma tiene ordinal > 0).

CREATE TABLE IF NOT EXISTS expediente_aip_version (
    expediente_id      UUID NOT NULL REFERENCES expediente_aip(expediente_id),
    version            INTEGER NOT NULL CHECK (version >= 1),
    aip_id             UUID NOT NULL UNIQUE,
    indice_file_id     UUID NOT NULL,
    indice_xml_sha256  CHAR(64) NOT NULL,
    firma_id           UUID,
    firma_ordinal      SMALLINT,
    motivo             TEXT NOT NULL CHECK (motivo IN ('ingesta', 'renovacion_lta')),
    sustituye_a_aip_id UUID,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (expediente_id, version),
    UNIQUE (expediente_id, indice_xml_sha256),
    CHECK ((version = 1) = (sustituye_a_aip_id IS NULL))
);

ALTER TABLE expediente_aip ADD COLUMN IF NOT EXISTS aip_vigente_id UUID;
ALTER TABLE expediente_aip ADD COLUMN IF NOT EXISTS indice_xml_sha256 CHAR(64);
ALTER TABLE expediente_aip ADD COLUMN IF NOT EXISTS reempaque_intentos INTEGER NOT NULL DEFAULT 0;
ALTER TABLE expediente_aip ADD COLUMN IF NOT EXISTS reempaque_proximo_at TIMESTAMPTZ;
ALTER TABLE expediente_aip ADD COLUMN IF NOT EXISTS reempaque_ultimo_error TEXT;
ALTER TABLE expediente_aip ADD COLUMN IF NOT EXISTS reempaque_agotado BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS ix_expediente_aip_reempaque ON expediente_aip (reempaque_proximo_at)
    WHERE estado = 'empaquetado' AND reempaque_agotado = FALSE;

CREATE OR REPLACE FUNCTION reject_expediente_aip_version_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'expediente_aip_version is append-only (historia de versiones del AIP inmutable)';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_expediente_aip_version_immutable ON expediente_aip_version;
CREATE TRIGGER trg_expediente_aip_version_immutable
    BEFORE UPDATE OR DELETE ON expediente_aip_version
    FOR EACH ROW EXECUTE FUNCTION reject_expediente_aip_version_mutation();
