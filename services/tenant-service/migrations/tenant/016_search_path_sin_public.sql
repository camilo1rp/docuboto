-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 016: re-fija el `search_path` de las funciones del schema sin `public`
-- (ADR-002, addendum C-7 / SEG #12).
--
-- El runner capturaba `SET search_path FROM CURRENT` = `"tenant_x", public` en las
-- funciones creadas por migraciones anteriores (dependencia por código, ciclos de
-- dependencias, TRD productora...). Con el runner nuevo (solo el tenant) las nuevas ya
-- nacen bien; aquí se corrigen las existentes SIN depender del orden de los servicios:
-- toda función del schema cuyo `proconfig` fije un `search_path` que nombre `public`.
-- En un tenant nuevo es un no-op.

DO $$
DECLARE
    f regprocedure;
BEGIN
    FOR f IN
        SELECT p.oid::regprocedure
          FROM pg_proc p
         WHERE p.pronamespace = current_schema()::regnamespace
           AND EXISTS (
                SELECT 1 FROM unnest(p.proconfig) AS c
                 WHERE c LIKE 'search_path=%'
                   AND c ~ '(^|[=, ])"?public"?($|[, ])'
           )
    LOOP
        EXECUTE format('ALTER FUNCTION %s SET search_path = %I', f, current_schema());
    END LOOP;
END $$;
