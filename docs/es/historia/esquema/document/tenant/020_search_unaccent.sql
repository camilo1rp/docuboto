-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- El índice de búsqueda de radicados deja de distinguir acentos (ADR-014).
--
-- Ver `global/002_spanish_unaccent.sql` para el porqué. Aquí se RECREA la
-- columna generada: su expresión forma parte de la definición de la columna, y
-- Postgres no permite alterarla en sitio. Al recrearla, el `search_vector` de
-- cada fila se recalcula — que es justamente el backfill que hace falta, porque
-- un índice viejo con acentos seguiría sin encontrar nada aunque la consulta
-- cambie. Las dos mitades tienen que moverse juntas.
--
-- El `regconfig` va CUALIFICADO y con cast explícito (`'public.spanish_unaccent'::regconfig`):
-- `to_tsvector(text, text)` es STABLE y una columna generada exige IMMUTABLE, así
-- que la variante de dos textos ni siquiera se acepta aquí.
--
-- Idempotente: el guard mira si la columna ya usa la configuración nueva. Sin él,
-- reejecutar la migración recalcularía el índice entero de una entidad grande sin
-- necesidad.

DO $$
DECLARE
    v_expr text;
BEGIN
    SELECT pg_get_expr(d.adbin, d.adrelid)
      INTO v_expr
      FROM pg_attrdef d
      JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
      JOIN pg_class c ON c.oid = d.adrelid
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE c.relname = 'radicados'
       AND a.attname = 'search_vector'
       AND n.nspname = current_schema();

    IF v_expr IS NULL OR v_expr NOT LIKE '%spanish_unaccent%' THEN
        ALTER TABLE radicados DROP COLUMN IF EXISTS search_vector;

        ALTER TABLE radicados
            ADD COLUMN search_vector tsvector
            GENERATED ALWAYS AS (
                setweight(to_tsvector('public.spanish_unaccent'::regconfig, coalesce(tracking_number, '')), 'A') ||
                setweight(to_tsvector('public.spanish_unaccent'::regconfig, coalesce(subject, '')), 'B') ||
                setweight(to_tsvector('public.spanish_unaccent'::regconfig, coalesce(sender_name, '')), 'C') ||
                setweight(to_tsvector('public.spanish_unaccent'::regconfig, coalesce(sender_entity, '')), 'C') ||
                setweight(to_tsvector('public.spanish_unaccent'::regconfig, coalesce(dest_dept, '')), 'D') ||
                setweight(to_tsvector('public.spanish_unaccent'::regconfig, coalesce(observations, '')), 'D')
            ) STORED;

        -- El índice cae con la columna; se vuelve a crear con el mismo nombre.
        CREATE INDEX IF NOT EXISTS ix_radicados_search_vector
            ON radicados USING GIN (search_vector);
    END IF;
END
$$;
