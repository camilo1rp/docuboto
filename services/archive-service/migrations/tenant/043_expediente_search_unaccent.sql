-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- La búsqueda de expedientes deja de distinguir acentos (ADR-014).
--
-- `005_expediente_search.sql` decía «sin pg_trgm/unaccent para no depender de
-- extensiones». La razón no se sostiene: `document-service` instala las dos en
-- `public` (`global/001_extensions.sql`) sobre esta misma base de datos, así que
-- la dependencia ya existía y lo único que producía la decisión era que buscar
-- un expediente se comportara distinto que buscar un radicado — el mismo texto,
-- dos resultados diferentes según la pantalla. Eso es peor que depender de una
-- extensión: es que el sistema se contradiga a sí mismo.
--
-- La configuración `public.spanish_unaccent` la crea
-- `document-service/migrations/global/002_spanish_unaccent.sql`. El orden entre
-- servicios lo garantiza el runner de provisión (`scripts/init_tenant.py`), que
-- aplica las globales de document antes que las de tenant de archive; si aun así
-- faltara, esta migración falla en voz alta en vez de dejar el índice a medias.

DO $$
DECLARE
    v_expr text;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_ts_config c
          JOIN pg_namespace n ON n.oid = c.cfgnamespace
         WHERE c.cfgname = 'spanish_unaccent' AND n.nspname = 'public'
    ) THEN
        RAISE EXCEPTION
            'Falta la configuración public.spanish_unaccent (document-service/migrations/global/002)';
    END IF;

    SELECT pg_get_expr(d.adbin, d.adrelid)
      INTO v_expr
      FROM pg_attrdef d
      JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
      JOIN pg_class c ON c.oid = d.adrelid
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE c.relname = 'expedientes'
       AND a.attname = 'search_vector'
       AND n.nspname = current_schema();

    IF v_expr IS NULL OR v_expr NOT LIKE '%spanish_unaccent%' THEN
        ALTER TABLE expedientes DROP COLUMN IF EXISTS search_vector;

        ALTER TABLE expedientes
            ADD COLUMN search_vector tsvector
            GENERATED ALWAYS AS (
                setweight(to_tsvector('public.spanish_unaccent'::regconfig, coalesce(code, '')), 'A') ||
                setweight(to_tsvector('public.spanish_unaccent'::regconfig, coalesce(name, '')), 'B') ||
                setweight(to_tsvector('public.spanish_unaccent'::regconfig, coalesce(description, '')), 'C')
            ) STORED;

        CREATE INDEX IF NOT EXISTS ix_expedientes_search
            ON expedientes USING GIN (search_vector);
    END IF;
END
$$;
