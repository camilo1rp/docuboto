-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E10 Increment A, RF-PRE-03, AGN 001/2024 Art. 4.3.2.6):
-- registro de objetos protegidos bajo WORM real (MinIO Object Lock).
--
-- Cada fila es la constancia de que una copia INMUTABLE de un artefacto (hoy: el
-- índice electrónico firmado — E15/E06; el AIP es un Increment B posterior) fue
-- subida a un bucket con Object Lock habilitado, con retención derivada de la TRD.
-- Sirve al gate de disposición final (`purge_for_disposition`): mientras exista
-- una fila con `retain_until` futuro o `legal_hold = true` para un expediente, la
-- purga física de sus archivos queda bloqueada.

CREATE TABLE IF NOT EXISTS preservacion_worm_objeto (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    expediente_id   TEXT NOT NULL,
    tipo            TEXT NOT NULL CHECK (tipo IN ('indice', 'aip')),
    bucket          TEXT NOT NULL,
    object_key      TEXT NOT NULL,
    version_id      TEXT,
    sha256          CHAR(64) NOT NULL,
    retain_until    TIMESTAMPTZ NOT NULL,
    retention_mode  TEXT NOT NULL,
    legal_hold      BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_preservacion_worm_expediente
    ON preservacion_worm_objeto (expediente_id);
