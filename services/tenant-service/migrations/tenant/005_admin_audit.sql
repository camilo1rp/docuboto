-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 005: auditoría de cambios administrativos (RT-14 §4.2) — E14.
-- Específica del dominio administrativo (params/dependencias/festivos); la auditoría
-- transversal del sistema vive en public.audit_log (ADR-008). search_path = tenant_{slug}.

CREATE TABLE IF NOT EXISTS admin_audit (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity      VARCHAR(50)  NOT NULL,   -- 'param' | 'dependencia' | 'non_business_day'
    entity_id   VARCHAR(100),
    action      VARCHAR(20)  NOT NULL,   -- create | update | delete
    old_value   JSONB,
    new_value   JSONB,
    actor_id    UUID,                    -- user_id del JWT
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_admin_audit_entity ON admin_audit (entity, created_at);
