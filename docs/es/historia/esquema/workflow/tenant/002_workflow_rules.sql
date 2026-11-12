-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Workflow Rules Engine — configurable no-code routing rules
-- Rules are evaluated on each new radicado to auto-assign to departments

CREATE TABLE IF NOT EXISTS workflow_rules (
    id          UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    name        VARCHAR(200) NOT NULL,
    description TEXT,
    is_active   BOOLEAN     NOT NULL DEFAULT true,
    priority    INTEGER     NOT NULL DEFAULT 100, -- Lower = higher priority
    -- Conditions (all must match — AND logic)
    doc_type    VARCHAR(1),                       -- E, S, I or NULL = any
    doc_class   TEXT,                             -- exact match or NULL = any
    dest_dept   TEXT,                             -- exact match or NULL = any
    subject_contains TEXT,                        -- substring match or NULL = skip
    -- Action
    assign_to_dept  TEXT        NOT NULL,
    assign_notes    TEXT,
    created_by  UUID        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_workflow_rules_active_priority
    ON workflow_rules (is_active, priority)
    WHERE is_active = true;

-- Audit log of rule activations
CREATE TABLE IF NOT EXISTS workflow_rule_activations (
    id              UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    rule_id         UUID        NOT NULL REFERENCES workflow_rules(id) ON DELETE CASCADE,
    radicado_id     UUID        NOT NULL,
    tracking_number VARCHAR(50) NOT NULL,
    activated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_rule_activations_radicado
    ON workflow_rule_activations (radicado_id);
CREATE INDEX IF NOT EXISTS ix_rule_activations_rule
    ON workflow_rule_activations (rule_id);
