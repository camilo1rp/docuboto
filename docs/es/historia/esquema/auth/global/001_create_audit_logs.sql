-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migration 001: create auth_audit_logs table

CREATE TABLE IF NOT EXISTS public.auth_audit_logs (
    id          UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    event_type  VARCHAR(50) NOT NULL,
    username    VARCHAR(150),
    tenant_slug VARCHAR(100),
    ip_address  VARCHAR(45),
    user_agent  TEXT,
    detail      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_auth_audit_logs_event_type  ON public.auth_audit_logs (event_type);
CREATE INDEX IF NOT EXISTS ix_auth_audit_logs_username    ON public.auth_audit_logs (username);
CREATE INDEX IF NOT EXISTS ix_auth_audit_logs_tenant_slug ON public.auth_audit_logs (tenant_slug);
CREATE INDEX IF NOT EXISTS ix_auth_audit_logs_created_at  ON public.auth_audit_logs (created_at);
