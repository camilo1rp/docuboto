-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E05, RF-FLU-07): vencimientos y semáforos del trámite.
--
-- `due_at` = fecha/hora límite del paso (derivada del plazo del radicado). El
-- semáforo (verde/amarillo/rojo/vencido) se calcula en lectura a partir de due_at.
-- `overdue_notified` evita alertar dos veces el mismo paso vencido (dedup del
-- barrido que emite eventos a E16).

ALTER TABLE flow_steps
    ADD COLUMN IF NOT EXISTS due_at TIMESTAMPTZ;

ALTER TABLE flow_steps
    ADD COLUMN IF NOT EXISTS overdue_notified BOOLEAN NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS ix_flow_steps_due
    ON flow_steps (status, due_at)
    WHERE due_at IS NOT NULL;
