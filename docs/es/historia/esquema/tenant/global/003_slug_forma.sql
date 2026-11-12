-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Global migration 003: la base exige la misma forma de slug que la aplicación
-- (orpycamcp_common.tenant.SLUG_PATTERN, AREA-02-05).
--
-- El validador común es más estricto que las copias que sustituyó (mínimo 2, máximo
-- 54, sin guion inicial). Una entidad ya registrada con un slug fuera de esa forma
-- haría que tenant_schema() fallara en TODOS los servicios y su fondo quedara
-- inaccesible (Ley 594/2000 art. 27). Por eso, ANTES de endurecer, se aborta la
-- migración —con la lista de culpables— si existe alguna: que un humano decida
-- (renombrar slug y schema) en vez de descubrirlo en producción. Fallo cerrado y
-- visible; no se toca ningún dato.

DO $$
DECLARE
    malos text;
BEGIN
    SELECT string_agg(format('%L', slug), ', ' ORDER BY slug)
      INTO malos
      FROM public.tenants
     WHERE slug !~ '^[a-z0-9][a-z0-9-]{1,53}$';
    IF malos IS NOT NULL THEN
        RAISE EXCEPTION
            'public.tenants tiene slugs fuera de la forma ^[a-z0-9][a-z0-9-]{1,53}$: %. '
            'Renómbrelos (slug y schema tenant_*) antes de migrar: ningún servicio '
            'los aceptaría y el fondo de esas entidades quedaría inaccesible.', malos;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'tenants_slug_forma_chk'
           AND conrelid = 'public.tenants'::regclass
    ) THEN
        ALTER TABLE public.tenants
            ADD CONSTRAINT tenants_slug_forma_chk
            CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,53}$');
    END IF;
END
$$;
