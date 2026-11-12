-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Security levels and the clearance of each group.

-- ── security_levels ───────────────────────────────────────────────────────────

CREATE TABLE security_levels (
    code smallint NOT NULL,
    nombre character varying(40) NOT NULL,
    CONSTRAINT security_levels_nombre_key UNIQUE (nombre),
    CONSTRAINT security_levels_pkey PRIMARY KEY (code)
);

INSERT INTO security_levels (code, nombre) VALUES
    ('1', 'PUBLICA'),
    ('2', 'RESERVADA'),
    ('3', 'CLASIFICADA');

-- ── role_clearance ────────────────────────────────────────────────────────────

CREATE TABLE role_clearance (
    group_id uuid NOT NULL,
    max_level smallint NOT NULL,
    CONSTRAINT role_clearance_pkey PRIMARY KEY (group_id),
    CONSTRAINT role_clearance_group_id_fkey FOREIGN KEY (group_id) REFERENCES auth_groups(id) ON DELETE CASCADE,
    CONSTRAINT role_clearance_max_level_fkey FOREIGN KEY (max_level) REFERENCES security_levels(code)
);
