-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Entity parameters and the administration audit trail.

-- ── sgd_param_admin ───────────────────────────────────────────────────────────

CREATE TABLE sgd_param_admin (
    key character varying(100) NOT NULL,
    value jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by uuid,
    CONSTRAINT sgd_param_admin_pkey PRIMARY KEY (key)
);

INSERT INTO sgd_param_admin (key, value, updated_by) VALUES
    ('archivado_requiere_exp', 'false', NULL),
    ('consec_digits', '6', NULL),
    ('depe_digits', '3', NULL),
    ('dias_bloqueo', '15', NULL),
    ('entidad_direccion', '""', NULL),
    ('entidad_email', '""', NULL),
    ('entidad_nit', '""', NULL),
    ('entidad_nombre', '""', NULL),
    ('entidad_sigla', '""', NULL),
    ('entidad_telefono', '""', NULL),
    ('entidad_web', '""', NULL),
    ('expediente_number_format', '"EXP-{YEAR}-{SEQ:04d}"', NULL),
    ('firma_digital_provider', 'null', NULL),
    ('firma_personal_require_xades', 'null', NULL),
    ('formato_numero_radicado', '"alfanumerico"', NULL),
    ('notificaciones_mail', 'true', NULL),
    ('politica_tratamiento_texto', '""', NULL),
    ('politica_tratamiento_url', '""', NULL),
    ('politica_tratamiento_version', '""', NULL),
    ('reasigna_requiere_exp', 'false', NULL),
    ('unicoconsecutivo', 'false', NULL);

-- ── admin_audit ───────────────────────────────────────────────────────────────

CREATE TABLE admin_audit (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    entity character varying(50) NOT NULL,
    entity_id character varying(100),
    action character varying(20) NOT NULL,
    old_value jsonb,
    new_value jsonb,
    actor_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT admin_audit_pkey PRIMARY KEY (id)
);

CREATE INDEX ix_admin_audit_entity ON admin_audit USING btree (entity, created_at);
