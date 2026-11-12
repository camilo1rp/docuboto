-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Huella de `capabilities` que el administrador aceptó, por nivel (ADR-045 §4).
-- La suspensión se levanta cuando cada nivel habilitado tiene la huella vigente.
-- No guarda la declaración en claro: esa queda en el asiento de auditoría.
--
-- Idempotente. Rollback:
--   DROP TABLE IF EXISTS intelligence_level_acceptance;

CREATE TABLE IF NOT EXISTS intelligence_level_acceptance (
    level                SMALLINT PRIMARY KEY CHECK (level BETWEEN 1 AND 3),
    capabilities_sha256  TEXT NOT NULL CHECK (capabilities_sha256 ~ '^[0-9a-f]{64}$'),
    accepted_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
