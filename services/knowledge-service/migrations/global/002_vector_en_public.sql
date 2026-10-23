-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Global migration 002: `vector` vive en `public` (ADR-002, addendum C-7 / SEG #12).
--
-- El SQL de ejecución nombra `$1::public.vector` y `OPERATOR(public.<=>)`, y las
-- migraciones de tenant `public.vector(384)` / `public.vector_cosine_ops`, porque el
-- `search_path` es solo el tenant. Fallo legible al migrar, no a las 3 de la mañana.

DO $$
DECLARE
    esquema name;
BEGIN
    SELECT n.nspname INTO esquema
      FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace
     WHERE e.extname = 'vector';
    IF esquema IS NULL THEN
        RAISE EXCEPTION 'extension_ausente: la extensión vector no está instalada (ADR-002: debe vivir en public)';
    ELSIF esquema <> 'public' THEN
        RAISE EXCEPTION 'extension_fuera_de_public: vector está en el schema % y el SQL la nombra public.vector (ADR-002, addendum C-7)', esquema;
    END IF;
END $$;
