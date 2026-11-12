-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E05, RF-FLU-03): tipo documental en el paso de trámite.
--
-- Se denormaliza `doc_type` (E/S/I) del radicado en el paso para poder servir
-- bandejas tipadas (entrada/salida/internos) sin que workflow-service consulte
-- la tabla de radicados de document-service (autonomía del servicio). Se fija al
-- asignar (manual o por distribución automática).

ALTER TABLE flow_steps
    ADD COLUMN IF NOT EXISTS doc_type VARCHAR(1);

CREATE INDEX IF NOT EXISTS ix_flow_steps_inbox
    ON flow_steps (to_dept, status, doc_type);
