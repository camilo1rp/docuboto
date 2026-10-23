-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Global migration 005: las extensiones que el SQL nombra calificado viven en `public`
-- (ADR-002, addendum C-7 / SEG #12).
--
-- Con el `search_path` de solo-el-tenant, el SQL de ejecución y las migraciones de tenant
-- nombran `public.unaccent`, `public.gin_trgm_ops`, `public.digest`... Si una extensión
-- estuviera en otro schema el síntoma sería un `type/function "public.x" does not exist`
-- en la primera petición. Esta migración convierte ese acoplamiento en un fallo legible
-- AL MIGRAR. Cada servicio comprueba lo suyo: document, `pg_trgm` y `unaccent`
-- (001_extensions.sql); knowledge comprueba `vector`; auth crea `pgcrypto` WITH SCHEMA public (tenant/012).

DO $$
DECLARE
    ext text;
    esquema name;
BEGIN
    FOREACH ext IN ARRAY ARRAY['pg_trgm', 'unaccent'] LOOP
        SELECT n.nspname INTO esquema
          FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace
         WHERE e.extname = ext;
        IF esquema IS NULL THEN
            RAISE EXCEPTION 'extension_ausente: la extensión % no está instalada (ADR-002: debe vivir en public)', ext;
        ELSIF esquema <> 'public' THEN
            RAISE EXCEPTION 'extension_fuera_de_public: % está en el schema % y el SQL la nombra public.% (ADR-002, addendum C-7)', ext, esquema, ext;
        END IF;
    END LOOP;
END $$;
