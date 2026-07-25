-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Backfill de los CHECK de `firma` (`firma_nivel_conformidad_check`,
-- `firma_xades_level_check`, E06) en los tenants ya provisionados que los PERDIERON
-- por el bug de idempotencia de 008_firma_xades.sql (guard por `conname` global,
-- sin filtrar por schema: `conname` se repite entre tenants, así que el guard veía
-- la constraint de otro tenant y la omitía ⇒ tenants #2+ sin el CHECK).
--
-- 008 ya está corregida en su sitio (para provisiones frescas), pero el runner
-- registra las migraciones por `(schema, filename)` y no re-aplica 008 a un tenant
-- que ya la tiene marcada — de ahí esta reparación, aplicada a TODOS los tenants
-- (los que ya tienen las constraints las ven presentes y no hacen nada). Acotada al
-- schema activo con `current_schema()`. Si un tenant tuviera filas fuera de dominio,
-- el ADD CONSTRAINT falla ruidoso — correcto (no enmascarar datos corruptos).

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE c.conname = 'firma_nivel_conformidad_check'
           AND n.nspname = current_schema()
    ) THEN
        ALTER TABLE firma
            ADD CONSTRAINT firma_nivel_conformidad_check
            CHECK (nivel_conformidad IS NULL OR nivel_conformidad IN (
                'firma_electronica_nativa',
                'xades_b_no_acreditado',
                'xades_b_acreditado'
            ));
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE c.conname = 'firma_xades_level_check'
           AND n.nspname = current_schema()
    ) THEN
        ALTER TABLE firma
            ADD CONSTRAINT firma_xades_level_check
            CHECK (xades_level IS NULL OR xades_level IN (
                'XAdES-B',
                'XAdES-T',
                'XAdES-LT',
                'XAdES-LTA'
            ));
    END IF;
END $$;
