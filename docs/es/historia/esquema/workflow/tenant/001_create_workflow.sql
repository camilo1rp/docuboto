-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Flow steps track every movement of a radicado between dependencias.
-- This table lives inside each tenant's schema (tenant_{slug}).
CREATE TABLE IF NOT EXISTS flow_steps (
    id              UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    radicado_id     UUID        NOT NULL,
    tracking_number VARCHAR(50) NOT NULL,
    step_number     INTEGER     NOT NULL,
    from_dept       VARCHAR(100),
    to_dept         VARCHAR(100) NOT NULL,
    assigned_to     UUID,
    assigned_by     UUID        NOT NULL,
    action          VARCHAR(30) NOT NULL,
    notes           TEXT,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_flow_steps_radicado_id     ON flow_steps (radicado_id);
CREATE INDEX IF NOT EXISTS ix_flow_steps_tracking_number ON flow_steps (tracking_number);
CREATE INDEX IF NOT EXISTS ix_flow_steps_to_dept         ON flow_steps (to_dept);
CREATE INDEX IF NOT EXISTS ix_flow_steps_status          ON flow_steps (status);
CREATE INDEX IF NOT EXISTS ix_flow_steps_assigned_to     ON flow_steps (assigned_to);
