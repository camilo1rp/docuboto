-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración global (schema public): configuración de búsqueda en español que
-- IGNORA LOS ACENTOS.
--
-- La extensión `unaccent` se instalaba desde el primer día (001_extensions.sql
-- lo dice: «normalización de acentos para FTS en español») y el ADR-014 la
-- declara parte de la decisión de búsqueda. Nunca llegó a usarse: tanto el
-- `search_vector` como la consulta seguían con la configuración `spanish` a
-- secas. El resultado, comprobado pulsando botones: buscar «certificacion»
-- devolvía CERO radicados mientras «certificación» devolvía tres. En un sistema
-- que usan funcionarios colombianos escribiendo deprisa, eso no es un caso
-- exótico — es el caso normal, y falla en silencio: la pantalla dice «Sin
-- resultados», que es indistinguible de «no existe».
--
-- Va en `public` y no por-tenant a propósito, igual que las extensiones: una
-- configuración de texto por entidad serían copias redundantes de lo mismo, y
-- las columnas generadas la referencian por nombre cualificado.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_ts_config c
          JOIN pg_namespace n ON n.oid = c.cfgnamespace
         WHERE c.cfgname = 'spanish_unaccent' AND n.nspname = 'public'
    ) THEN
        CREATE TEXT SEARCH CONFIGURATION public.spanish_unaccent (COPY = pg_catalog.spanish);
        -- `unaccent` ANTES de `spanish_stem`: primero se quitan los acentos,
        -- después se reduce a la raíz. Al revés, la raíz conservaría el acento
        -- y el problema seguiría igual.
        ALTER TEXT SEARCH CONFIGURATION public.spanish_unaccent
            ALTER MAPPING FOR hword, hword_part, word
            WITH unaccent, spanish_stem;
    END IF;
END
$$;
