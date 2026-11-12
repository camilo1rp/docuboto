-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Global migration 006: elección de inteligencia por entidad (ADR-045 §4, fase D1).
--
-- Toda entidad nace en `local` y solo transmite PÚBLICA (1). RESERVADA (2) y
-- CLASIFICADA (3) salen después, con el acto del administrador. Los metadatos de
-- plantilla no salen hasta que ese mismo acto encienda el interruptor. El rol de
-- la aplicación ya tiene UPDATE sobre public.tenants: no hace falta grant nuevo.
--
-- Idempotente: una reejecución no vuelve a poner `local` a quien ya eligió `ppi`.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'tenants'
           AND column_name = 'intelligence_provider'
    ) THEN
        ALTER TABLE public.tenants
            ADD COLUMN intelligence_provider TEXT NOT NULL DEFAULT 'local';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'tenants_intelligence_provider_chk'
           AND conrelid = 'public.tenants'::regclass
    ) THEN
        ALTER TABLE public.tenants
            ADD CONSTRAINT tenants_intelligence_provider_chk
            CHECK (intelligence_provider IN ('local', 'ppi'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'tenants'
           AND column_name = 'intelligence_levels'
    ) THEN
        ALTER TABLE public.tenants
            ADD COLUMN intelligence_levels SMALLINT[] NOT NULL DEFAULT '{1}';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'tenants_intelligence_levels_chk'
           AND conrelid = 'public.tenants'::regclass
    ) THEN
        ALTER TABLE public.tenants
            ADD CONSTRAINT tenants_intelligence_levels_chk
            CHECK (intelligence_levels <@ ARRAY[1, 2, 3]::smallint[]);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'tenants'
           AND column_name = 'intelligence_send_metadata'
    ) THEN
        ALTER TABLE public.tenants
            ADD COLUMN intelligence_send_metadata BOOLEAN NOT NULL DEFAULT false;
    END IF;
END $$;
