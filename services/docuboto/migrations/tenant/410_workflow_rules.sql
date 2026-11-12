-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Routing rules and their activations.

-- ── workflow_rules ────────────────────────────────────────────────────────────

CREATE TABLE workflow_rules (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(200) NOT NULL,
    description text,
    is_active boolean DEFAULT true NOT NULL,
    priority integer DEFAULT 100 NOT NULL,
    doc_type character varying(1),
    doc_class text,
    dest_dept text,
    subject_contains text,
    assign_to_dept text NOT NULL,
    assign_notes text,
    created_by uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    conditions jsonb DEFAULT '[]'::jsonb NOT NULL,
    match_mode character varying(3) DEFAULT 'all'::character varying NOT NULL,
    assign_to_dept_code integer,
    dest_dept_code integer,
    CONSTRAINT ck_workflow_rules_assign_code CHECK ((assign_to_dept_code IS NOT NULL)),
    CONSTRAINT ck_workflow_rules_dest_code CHECK (((dest_dept IS NULL) OR (dest_dept_code IS NOT NULL))),
    CONSTRAINT workflow_rules_pkey PRIMARY KEY (id)
);

CREATE INDEX ix_workflow_rules_active_priority ON workflow_rules USING btree (is_active, priority) WHERE (is_active = true);

-- ── workflow_rule_activations ─────────────────────────────────────────────────

CREATE TABLE workflow_rule_activations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    rule_id uuid NOT NULL,
    radicado_id uuid NOT NULL,
    tracking_number character varying(50) NOT NULL,
    activated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT workflow_rule_activations_pkey PRIMARY KEY (id),
    CONSTRAINT workflow_rule_activations_rule_id_fkey FOREIGN KEY (rule_id) REFERENCES workflow_rules(id) ON DELETE CASCADE
);

CREATE INDEX ix_rule_activations_radicado ON workflow_rule_activations USING btree (radicado_id);
CREATE INDEX ix_rule_activations_rule ON workflow_rule_activations USING btree (rule_id);
