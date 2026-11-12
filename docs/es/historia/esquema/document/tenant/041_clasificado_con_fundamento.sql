-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


-- Tenant migration 041 (AREA-09-04): «nunca NULL» en el índice de clasificados.
--
-- El índice de información clasificada y reservada (Ley 1712/2014 art. 20) promete un
-- fundamento por cada fila. Los caminos de escritura del API lo exigen (schemas de
-- radicación y reclasificación), pero la importación INT-05 admitía `nivel >= 2` con
-- `clasificacion.fundamento` nulo, y la base no lo impedía: bastaba un INSERT directo.
--
-- 1) Backfill: las filas ya clasificadas sin fundamento reciben un texto EXPLÍCITO
--    de pendiente (no inventa una causal jurídica: dice que falta, y aparece tal cual
--    en el índice para que la entidad lo motive). El histórico inmutable no se toca.
-- 2) CHECK (nivel_seguridad < 2 OR fundamento no vacío), NOT VALID + VALIDATE: rige
--    desde ya para toda escritura y se valida lo existente tras el backfill.
--
-- Idempotente. Rollback:
--   ALTER TABLE radicados DROP CONSTRAINT IF EXISTS radicados_clasificado_con_fundamento;

UPDATE radicados
   SET clasificacion_fundamento =
       'PENDIENTE DE MOTIVAR: clasificado sin fundamento registrado (anterior a la exigencia de la Ley 1712/2014 art. 19/28)'
 WHERE nivel_seguridad >= 2
   AND (clasificacion_fundamento IS NULL OR btrim(clasificacion_fundamento) = '');

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'radicados_clasificado_con_fundamento'
           AND conrelid = format('%I.radicados', current_schema())::regclass
    ) THEN
        ALTER TABLE radicados
            ADD CONSTRAINT radicados_clasificado_con_fundamento
            CHECK (nivel_seguridad < 2
                   OR (clasificacion_fundamento IS NOT NULL
                       AND btrim(clasificacion_fundamento) <> '')) NOT VALID;
    END IF;
    ALTER TABLE radicados VALIDATE CONSTRAINT radicados_clasificado_con_fundamento;
END $$;
