-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Contadores de la pasarela (ADR-041, ADR-045). El libro ya guarda los envíos
-- y `intelligence_state` la suspensión. El rechazo de barrera y el respaldo
-- local no dejan fila: se anotan aquí, una fila por motivo cerrado, para que
-- el scrape los sume sin etiqueta de entidad. No guardan radicado ni persona.
--
-- Idempotente. Rollback:
--   DROP TABLE IF EXISTS intelligence_meter;

CREATE TABLE IF NOT EXISTS intelligence_meter (
    kind    TEXT NOT NULL,
    reason  TEXT NOT NULL,
    n       BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (kind, reason),
    CONSTRAINT chk_intelligence_meter_n CHECK (n >= 0),
    CONSTRAINT chk_intelligence_meter_motivo CHECK (
        (kind = 'barrier' AND reason IN ('suspendido', 'ausente', 'nivel'))
        OR (kind = 'fallback' AND reason = 'local')
    )
);
