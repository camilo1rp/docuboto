-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant 047 (B5, ronda de corrección, ADR-032). search_path = tenant_{slug}.
--
-- Enlaza la evidencia del backfill de la 046 con la decisión humana que la resolvió:
-- `historico_id` apunta a la fila de `dependencia_nombre_historico` (origen
-- 'mapeo_manual') cuyo (nombre, código) coincide. «Mapeado» NO es un valor de
-- `resultado`: se DERIVA de `historico_id IS NOT NULL`. `resultado` y el resto de
-- columnas que ancla la 046 en `audit_log` (payload.columnas: tabla, fila_id, columna,
-- nombre, codigo, candidatos, resultado) NO se tocan, para que el SHA-256 del ancla
-- siga reproduciéndose; `historico_id` es una columna NUEVA y queda fuera de ese hash.
--
-- La evidencia es de solo inserción: el trigger se aparta DENTRO de la transacción
-- solo para completar la columna nueva (NULL -> valor) y se verifica que quedó
-- habilitado. Idempotente (IS NULL): la 2.ª ejecución no cambia filas.
-- Rollback: DROP COLUMN historico_id.

ALTER TABLE dependencia_codigo_backfill_doc ADD COLUMN IF NOT EXISTS historico_id BIGINT;

DO $$
BEGIN
    IF to_regclass(format('%I.dependencia_nombre_historico', current_schema())) IS NULL THEN
        RETURN;  -- tenant-service 013 aún no corrió: no hay mapeos que enlazar
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM dependencia_codigo_backfill_doc e
          JOIN dependencia_nombre_historico h
            ON h.origen = 'mapeo_manual' AND h.nombre = e.nombre AND h.codigo = e.codigo
         WHERE e.resultado = 'resuelto' AND e.historico_id IS NULL) THEN
        RETURN;
    END IF;
    ALTER TABLE dependencia_codigo_backfill_doc DISABLE TRIGGER trg_dependencia_codigo_backfill_doc_immutable;
    UPDATE dependencia_codigo_backfill_doc e
       SET historico_id = h.id
      FROM (SELECT DISTINCT ON (nombre, codigo) id, nombre, codigo
              FROM dependencia_nombre_historico WHERE origen = 'mapeo_manual'
             ORDER BY nombre, codigo, id DESC) h
     WHERE e.resultado = 'resuelto' AND e.historico_id IS NULL
       AND h.nombre = e.nombre AND h.codigo = e.codigo;
    ALTER TABLE dependencia_codigo_backfill_doc ENABLE TRIGGER trg_dependencia_codigo_backfill_doc_immutable;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger
                    WHERE tgrelid = 'dependencia_codigo_backfill_doc'::regclass
                      AND tgname = 'trg_dependencia_codigo_backfill_doc_immutable' AND tgenabled = 'O') THEN
        RAISE EXCEPTION 'trg_dependencia_codigo_backfill_doc_immutable no quedó habilitado';
    END IF;
END $$;
