-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 002: parámetros de configuración key-value JSONB — E14.
-- search_path = tenant_{slug} (ADR-012).

CREATE TABLE IF NOT EXISTS sgd_param_admin (
    key         VARCHAR(100) NOT NULL PRIMARY KEY,
    value       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by  UUID                                -- user_id del JWT
);

-- Parámetros por defecto (los consume E01: formato/dígitos del radicado, etc.).
INSERT INTO sgd_param_admin (key, value) VALUES
    ('unicoconsecutivo',         'false'::jsonb),
    ('formato_numero_radicado',  '"alfanumerico"'::jsonb),
    ('depe_digits',              '3'::jsonb),
    ('consec_digits',            '6'::jsonb),
    ('dias_bloqueo',             '15'::jsonb),
    ('expediente_number_format', '"EXP-{YEAR}-{SEQ:04d}"'::jsonb),
    ('notificaciones_mail',      'true'::jsonb),
    ('archivado_requiere_exp',   'false'::jsonb),
    ('reasigna_requiere_exp',    'false'::jsonb),
    ('firma_digital_provider',   'null'::jsonb)
ON CONFLICT (key) DO NOTHING;
