-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migration 001: create public.tenants table

CREATE TABLE IF NOT EXISTS public.tenants (
    id          UUID         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    slug        VARCHAR(100) NOT NULL UNIQUE,
    name        TEXT         NOT NULL,
    code        VARCHAR(20)  NOT NULL UNIQUE,
    status      VARCHAR(20)  NOT NULL DEFAULT 'active',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_tenants_slug       ON public.tenants (slug);
CREATE INDEX IF NOT EXISTS ix_tenants_code       ON public.tenants (code);
CREATE INDEX IF NOT EXISTS ix_tenants_status     ON public.tenants (status);
CREATE INDEX IF NOT EXISTS ix_tenants_created_at ON public.tenants (created_at);
