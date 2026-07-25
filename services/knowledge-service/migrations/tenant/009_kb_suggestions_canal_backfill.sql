-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Backfill de la constraint `chk_kb_suggestions_canal` (E21) en los tenants ya
-- provisionados que la PERDIERON por el bug de idempotencia de 008.
--
-- 008_kb_suggestions.sql tenía un guard `IF NOT EXISTS (SELECT 1 FROM pg_constraint
-- WHERE conname = 'chk_kb_suggestions_canal')` SIN filtrar por schema. Como
-- `pg_constraint.conname` se repite entre schemas de tenant, en cuanto UN tenant
-- creaba la constraint, el guard la daba por "existente" y la OMITÍA en todo tenant
-- provisionado después ⇒ su `kb_suggestions` quedaba sin el CHECK `canal IN (...)`.
--
-- 008 ya está corregida en su sitio (para provisiones frescas), pero el runner
-- registra las migraciones por `(schema, filename)` y NO re-aplica 008 a un tenant
-- que ya la tiene marcada — de ahí esta migración de reparación, que se aplica a
-- TODOS los tenants (los que ya tienen la constraint la ven presente y no hacen
-- nada). Acotada al schema activo con `current_schema()` (el patrón correcto).
--
-- No hay riesgo de datos que violen el CHECK: `canal` tiene DEFAULT 'system' y la
-- app solo escribe valores del dominio cerrado (api|ui|mcp|system); aun así, si un
-- tenant tuviera filas fuera de dominio, el ADD CONSTRAINT fallaría ruidoso — que es
-- el comportamiento correcto (no enmascarar datos corruptos).

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE c.conname = 'chk_kb_suggestions_canal'
           AND n.nspname = current_schema()
    ) THEN
        ALTER TABLE kb_suggestions
            ADD CONSTRAINT chk_kb_suggestions_canal
            CHECK (canal IN ('api', 'ui', 'mcp', 'system'));
    END IF;
END $$;
