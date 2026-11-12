-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
--
-- Extensions and the Spanish text search configuration. Both extensions live in
-- `public` because the SQL names them `public.unaccent` and `public.similarity`
-- from every tenant schema (ADR-002).

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA public;

DO $$
DECLARE
    ext text;
    esquema name;
BEGIN
    FOREACH ext IN ARRAY ARRAY['pg_trgm', 'unaccent'] LOOP
        SELECT n.nspname INTO esquema
          FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace
         WHERE e.extname = ext;
        IF esquema <> 'public' THEN
            RAISE EXCEPTION 'extension_fuera_de_public: % está en el schema % y el SQL la nombra public.% (ADR-002, addendum C-7)', ext, esquema, ext;
        END IF;
    END LOOP;
END $$;

CREATE TEXT SEARCH CONFIGURATION public.spanish_unaccent (
    PARSER = pg_catalog."default" );

ALTER TEXT SEARCH CONFIGURATION public.spanish_unaccent
    ADD MAPPING FOR asciiword WITH spanish_stem;

ALTER TEXT SEARCH CONFIGURATION public.spanish_unaccent
    ADD MAPPING FOR word WITH public.unaccent, spanish_stem;

ALTER TEXT SEARCH CONFIGURATION public.spanish_unaccent
    ADD MAPPING FOR numword WITH simple;

ALTER TEXT SEARCH CONFIGURATION public.spanish_unaccent
    ADD MAPPING FOR email WITH simple;

ALTER TEXT SEARCH CONFIGURATION public.spanish_unaccent
    ADD MAPPING FOR url WITH simple;

ALTER TEXT SEARCH CONFIGURATION public.spanish_unaccent
    ADD MAPPING FOR host WITH simple;

ALTER TEXT SEARCH CONFIGURATION public.spanish_unaccent
    ADD MAPPING FOR sfloat WITH simple;

ALTER TEXT SEARCH CONFIGURATION public.spanish_unaccent
    ADD MAPPING FOR version WITH simple;

ALTER TEXT SEARCH CONFIGURATION public.spanish_unaccent
    ADD MAPPING FOR hword_numpart WITH simple;

ALTER TEXT SEARCH CONFIGURATION public.spanish_unaccent
    ADD MAPPING FOR hword_part WITH public.unaccent, spanish_stem;

ALTER TEXT SEARCH CONFIGURATION public.spanish_unaccent
    ADD MAPPING FOR hword_asciipart WITH spanish_stem;

ALTER TEXT SEARCH CONFIGURATION public.spanish_unaccent
    ADD MAPPING FOR numhword WITH simple;

ALTER TEXT SEARCH CONFIGURATION public.spanish_unaccent
    ADD MAPPING FOR asciihword WITH spanish_stem;

ALTER TEXT SEARCH CONFIGURATION public.spanish_unaccent
    ADD MAPPING FOR hword WITH public.unaccent, spanish_stem;

ALTER TEXT SEARCH CONFIGURATION public.spanish_unaccent
    ADD MAPPING FOR url_path WITH simple;

ALTER TEXT SEARCH CONFIGURATION public.spanish_unaccent
    ADD MAPPING FOR file WITH simple;

ALTER TEXT SEARCH CONFIGURATION public.spanish_unaccent
    ADD MAPPING FOR "float" WITH simple;

ALTER TEXT SEARCH CONFIGURATION public.spanish_unaccent
    ADD MAPPING FOR "int" WITH simple;

ALTER TEXT SEARCH CONFIGURATION public.spanish_unaccent
    ADD MAPPING FOR uint WITH simple;
