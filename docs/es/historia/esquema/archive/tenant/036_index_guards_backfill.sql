-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Backfill de columnas/constraints que los tenants ya provisionados PERDIERON por
-- el bug de idempotencia de 016/017/019/035: sus guards `IF NOT EXISTS` sobre
-- `pg_constraint`/`information_schema.columns` filtraban por nombre de tabla/objeto
-- pero NO por schema. Como esos nombres se REPITEN entre schemas de tenant, en
-- cuanto UN tenant creaba el objeto el guard lo daba por existente y lo OMITÍA en
-- todo tenant provisionado después ⇒ tenants #2+ sin esas columnas/CHECK. Las
-- migraciones originales ya están corregidas en su sitio (acotadas a
-- `current_schema()`) para provisiones frescas; esta repara los tenants existentes
-- (el runner registra por `(schema, filename)` y no re-aplica las originales).
--
-- Todo acotado al schema activo. `ADD COLUMN IF NOT EXISTS` es nativamente
-- schema-scoped; los CHECK van en DO-block con guard por `current_schema()`.

-- 035: columna pdfa_profile (la más grave — su ausencia rompe el perfil PDF/A E11).
ALTER TABLE trd_series ADD COLUMN IF NOT EXISTS pdfa_profile TEXT;

DO $$
BEGIN
    -- expediente_radicados: tamano_bytes / folios (016) + dependencia_productora (019)
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE c.conname = 'expediente_radicados_tamano_bytes_check'
           AND n.nspname = current_schema()
    ) THEN
        ALTER TABLE expediente_radicados
            ADD CONSTRAINT expediente_radicados_tamano_bytes_check
            CHECK (tamano_bytes IS NULL OR tamano_bytes >= 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE c.conname = 'expediente_radicados_folios_check'
           AND n.nspname = current_schema()
    ) THEN
        ALTER TABLE expediente_radicados
            ADD CONSTRAINT expediente_radicados_folios_check
            CHECK (folios IS NULL OR folios >= 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE c.conname = 'expediente_radicados_dependencia_productora_pair_check'
           AND n.nspname = current_schema()
    ) THEN
        ALTER TABLE expediente_radicados
            ADD CONSTRAINT expediente_radicados_dependencia_productora_pair_check
            CHECK (
                (dependencia_productora_codigo IS NULL) =
                (dependencia_productora_nombre IS NULL)
            );
    END IF;

    -- expediente_indice_item: tamano_bytes / folios (017)
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE c.conname = 'expediente_indice_item_tamano_bytes_check'
           AND n.nspname = current_schema()
    ) THEN
        ALTER TABLE expediente_indice_item
            ADD CONSTRAINT expediente_indice_item_tamano_bytes_check
            CHECK (tamano_bytes IS NULL OR tamano_bytes >= 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE c.conname = 'expediente_indice_item_folios_check'
           AND n.nspname = current_schema()
    ) THEN
        ALTER TABLE expediente_indice_item
            ADD CONSTRAINT expediente_indice_item_folios_check
            CHECK (folios IS NULL OR folios >= 0);
    END IF;

    -- 035: trd_series pdfa_profile CHECK
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE c.conname = 'trd_series_pdfa_profile_check'
           AND n.nspname = current_schema()
    ) THEN
        ALTER TABLE trd_series
            ADD CONSTRAINT trd_series_pdfa_profile_check
            CHECK (pdfa_profile IS NULL OR pdfa_profile IN
                   ('1a', '1b', '2a', '2b', '2u', '3a', '3b', '3u'));
    END IF;
END $$;
