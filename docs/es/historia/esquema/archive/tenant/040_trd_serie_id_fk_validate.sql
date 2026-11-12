-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (ADR-025, Increment 2 -- cierre del pendiente declarado
-- en el comentario de la 037: "la validacion completa (VALIDATE CONSTRAINT)
-- va en el Increment 2, tras depurar huerfanos"). `expedientes_trd_serie_id_fkey`
-- quedó NOT VALID desde la 037 -- protege escrituras nuevas pero NO garantiza
-- que el historial sea consistente; sin `VALIDATE CONSTRAINT` el motor no
-- puede afirmar la integridad referencial completa que el FK promete.
--
-- Decisión sobre huérfanos (expedientes.trd_serie_id que no resuelve a
-- ninguna fila de trd_series): esta migración NO los depura -- ni a NULL ni
-- por DELETE. Un `trd_serie_id` huérfano es, en un sistema de archivo, un
-- vínculo con la posición de un expediente en la TRD que gobernó (o debió
-- gobernar) su retención/disposición; poner el campo a NULL destruiría esa
-- evidencia posicional sin dejar rastro de que existió, y borrar el
-- expediente sería borrar un documento público (Ley 594/2000). Ninguna de
-- las dos es una operación de migración legítima -- ambas son decisiones
-- archivísticas que le corresponden a un archivista/administrador con el
-- expediente concreto delante, no a un script.
--
-- En cambio: la propia 037 YA detecta y marca este caso. Su backfill de
-- `trd_serie_code` (líneas 186-190 de esa migración) hace un JOIN contra
-- `trd_series`; si `trd_serie_id` no resuelve, `trd_serie_code` queda NULL
-- -- y la 037 marca esas filas con `trd_revision_requerida = true,
-- trd_revision_motivo = 'serie_no_resoluble'` (líneas 217-222). Esta
-- migración reutiliza esa misma señal (no reinventa el criterio: audita
-- directamente contra `trd_series` para no depender de que `trd_serie_code`
-- se haya mantenido en sincronía) y, si encuentra huérfanos pendientes,
-- FALLA RUIDOSAMENTE listándolos -- no valida el FK, no toca los datos. Un
-- operador resuelve cada caso (motivo real por el que ese `trd_serie_id` no
-- resuelve: dato de prueba, migración de un tenant anterior a ADR-025,
-- corrupción puntual) y vuelve a correr esta misma migración -- es
-- re-ejecutable hasta que el historial quede limpio.
--
-- Idempotente + guard por `current_schema()` (no por el nombre desnudo de la
-- constraint -- lección de `documentos/specDrive` sobre guards que repiten
-- `conname`/columna entre esquemas y dejan al tenant #2 sin el objeto):
-- primero resuelve el `pg_constraint` de ESTE schema; si ya está validada,
-- no hace nada (es exactamente lo que hace `ALTER ... VALIDATE CONSTRAINT`
-- al re-ejecutarse, pero el guard evita incluso reintentar el escaneo).
DO $$
DECLARE
    v_validated  boolean;
    v_orphans    int;
    v_detail     text;
BEGIN
    SELECT c.convalidated
      INTO v_validated
      FROM pg_constraint c
      JOIN pg_class t ON t.oid = c.conrelid
      JOIN pg_namespace n ON n.oid = t.relnamespace
     WHERE n.nspname = current_schema()
       AND t.relname = 'expedientes'
       AND c.conname = 'expedientes_trd_serie_id_fkey';

    IF v_validated IS NULL THEN
        RAISE EXCEPTION
            'expedientes_trd_serie_id_fkey no existe en el schema % -- aplicar la migracion 037 antes que esta (ADR-025)',
            current_schema();
    END IF;

    IF v_validated THEN
        RAISE NOTICE 'expedientes_trd_serie_id_fkey ya esta VALIDATED en % -- nada que hacer', current_schema();
        RETURN;
    END IF;

    SELECT count(*),
           string_agg(
               format(
                   'expediente %s (status=%s, trd_serie_id=%s, trd_serie_code=%s, trd_revision_motivo=%s)',
                   e.id, e.status, e.trd_serie_id,
                   coalesce(e.trd_serie_code, 'NULL'),
                   coalesce(e.trd_revision_motivo, 'NULL')
               ),
               '; '
               ORDER BY e.id
           )
      INTO v_orphans, v_detail
      FROM expedientes e
     WHERE e.trd_serie_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM trd_series s WHERE s.id = e.trd_serie_id);

    IF v_orphans > 0 THEN
        RAISE EXCEPTION
            'No se valida expedientes_trd_serie_id_fkey en %: % expediente(s) con trd_serie_id huerfano (sin fila en trd_series). No se depuran automaticamente -- requiere decision archivistica caso por caso (ver comentario de esta migracion). Huerfanos: %',
            current_schema(), v_orphans, v_detail;
    END IF;

    ALTER TABLE expedientes VALIDATE CONSTRAINT expedientes_trd_serie_id_fkey;
    RAISE NOTICE 'expedientes_trd_serie_id_fkey validada en % (sin huerfanos)', current_schema();
END $$;

-- Rollback (referencia manual, no ejecutado por el runner):
--   ALTER TABLE expedientes ALTER CONSTRAINT expedientes_trd_serie_id_fkey NOT VALID;
--   (Postgres no soporta "invalidar" un FK ya validado; para revertir de
--   verdad hay que DROP + volver a ADD ... NOT VALID, como en la 037.)
