-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (B5, ADR-032): la dependencia se correlaciona por CÓDIGO.
--
--   1. `borrador.dest_dept_code`: hasta ahora el borrador solo guardaba el NOMBRE
--      del destino y, al radicar un Interno, `borrador_service` lo traducía con
--      `WHERE nombre = $1 ... ORDER BY codigo LIMIT 1`: con dos dependencias
--      homónimas radicaba hacia la de menor código, EN SILENCIO.
--   2. Backfill de `radicados.dest_dept_code`, `radicados.origin_dept_code` y
--      `borrador.dest_dept_code` donde el código es NULL y el nombre no, con
--      `resolver_codigo_dependencia(nombre, true)` (migración 013 de
--      tenant-service: UNA definición del criterio, incluye nombres anteriores y
--      mapeos manuales). Solo se escribe con EXACTAMENTE un candidato; con 0
--      (huérfano) o >=2 (ambiguo) la fila queda NULL y se deja evidencia.
--
-- Qué NO hace, a propósito:
--   * No hace RAISE por ambiguos/huérfanos: las columnas son INSTANTÁNEAS de
--     radicación (dato probatorio de un acto pasado) y un radicado no se bloquea
--     por un nombre viejo. La columna no pasa a NOT NULL.
--   * No toca `updated_at`: recalcular un derivado no es editar el documento.
--   * No toca `flujo_dept_code` (lo escribe SOLO workflow-service, ADR-029).
--
-- La evidencia es de SOLO INSERCIÓN (trigger). El asiento-ANCLA en `audit_log`
-- (conteo + SHA-256 del contenido ordenado) lo escribe el RUNNER (`_ANCLAS`), en
-- la misma transacción: nunca el SQL, que rompería la cadena de hashes.
--
-- Guarda de ORDEN: la función viene de tenant-service 013. Si falta y HAY filas
-- por rellenar -> RAISE (el runner no marca la migración aplicada y se reintenta
-- tras migrar tenant-service); sin filas pendientes no hay nada que perder.
--
-- Idempotente: la 2.ª ejecución no inserta evidencia (NOT EXISTS) ni cambia filas
-- (IS NULL). Rollback: DROP COLUMN borrador.dest_dept_code; los nombres nunca se tocaron.

ALTER TABLE borrador ADD COLUMN IF NOT EXISTS dest_dept_code INTEGER;

CREATE TABLE IF NOT EXISTS dependencia_codigo_backfill_doc (
    id         BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tabla      VARCHAR(40) NOT NULL,
    fila_id    UUID        NOT NULL,
    columna    VARCHAR(40) NOT NULL,
    nombre     TEXT        NOT NULL,
    codigo     INTEGER,
    candidatos INTEGER     NOT NULL,
    resultado  VARCHAR(10) NOT NULL CHECK (resultado IN ('resuelto', 'ambiguo', 'huerfano')),
    at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION reject_dependencia_codigo_backfill_doc_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'dependencia_codigo_backfill_doc is append-only (evidencia de la migración 046)';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_dependencia_codigo_backfill_doc_immutable ON dependencia_codigo_backfill_doc;
CREATE TRIGGER trg_dependencia_codigo_backfill_doc_immutable
    BEFORE UPDATE OR DELETE ON dependencia_codigo_backfill_doc
    FOR EACH ROW EXECUTE FUNCTION reject_dependencia_codigo_backfill_doc_mutation();

CREATE INDEX IF NOT EXISTS ix_dependencia_codigo_backfill_doc_fila
    ON dependencia_codigo_backfill_doc (tabla, fila_id, columna);

DO $$
DECLARE
    v_pendientes INTEGER;
    v_resueltas  INTEGER;
    v_sin_codigo INTEGER;
BEGIN
    SELECT (SELECT count(*) FROM radicados WHERE dest_dept_code IS NULL AND dest_dept IS NOT NULL)
         + (SELECT count(*) FROM radicados WHERE origin_dept_code IS NULL AND origin_dept IS NOT NULL)
         + (SELECT count(*) FROM borrador  WHERE dest_dept_code IS NULL AND dest_dept IS NOT NULL)
      INTO v_pendientes;

    IF v_pendientes = 0 THEN
        RETURN;
    END IF;

    IF to_regprocedure(format('%I.resolver_codigo_dependencia(text,boolean)', current_schema())) IS NULL THEN
        RAISE EXCEPTION
            '% tiene % fila(s) con nombre de dependencia sin código y no existe '
            'resolver_codigo_dependencia (migración 013 de tenant-service): migre '
            'tenant-service antes que document-service', current_schema(), v_pendientes;
    END IF;

    -- 1) Evidencia de TODO lo pendiente aún no evidenciado (resuelto/ambiguo/huérfano).
    INSERT INTO dependencia_codigo_backfill_doc
           (tabla, fila_id, columna, nombre, codigo, candidatos, resultado)
    SELECT p.tabla, p.fila_id, p.columna, p.nombre, x.codigo, x.candidatos,
           CASE WHEN x.candidatos = 1 THEN 'resuelto'
                WHEN x.candidatos = 0 THEN 'huerfano'
                ELSE 'ambiguo' END
      FROM (
            SELECT 'radicados'::text AS tabla, id AS fila_id, 'dest_dept'::text AS columna,
                   dest_dept::text AS nombre
              FROM radicados WHERE dest_dept_code IS NULL AND dest_dept IS NOT NULL
            UNION ALL
            SELECT 'radicados', id, 'origin_dept', origin_dept::text
              FROM radicados WHERE origin_dept_code IS NULL AND origin_dept IS NOT NULL
            UNION ALL
            SELECT 'borrador', id, 'dest_dept', dest_dept::text
              FROM borrador WHERE dest_dept_code IS NULL AND dest_dept IS NOT NULL
           ) p
     CROSS JOIN LATERAL resolver_codigo_dependencia(p.nombre, true) x
     WHERE NOT EXISTS (
            SELECT 1 FROM dependencia_codigo_backfill_doc e
             WHERE e.tabla = p.tabla AND e.fila_id = p.fila_id AND e.columna = p.columna)
     ORDER BY p.tabla, p.fila_id, p.columna;

    -- 2) Se escribe SOLO lo resuelto con un único candidato. Sin `updated_at`.
    UPDATE radicados r SET dest_dept_code = e.codigo
      FROM dependencia_codigo_backfill_doc e
     WHERE e.tabla = 'radicados' AND e.columna = 'dest_dept' AND e.resultado = 'resuelto'
       AND e.fila_id = r.id AND r.dest_dept_code IS NULL AND r.dest_dept = e.nombre;
    GET DIAGNOSTICS v_resueltas = ROW_COUNT;

    UPDATE radicados r SET origin_dept_code = e.codigo
      FROM dependencia_codigo_backfill_doc e
     WHERE e.tabla = 'radicados' AND e.columna = 'origin_dept' AND e.resultado = 'resuelto'
       AND e.fila_id = r.id AND r.origin_dept_code IS NULL AND r.origin_dept = e.nombre;

    UPDATE borrador b SET dest_dept_code = e.codigo
      FROM dependencia_codigo_backfill_doc e
     WHERE e.tabla = 'borrador' AND e.columna = 'dest_dept' AND e.resultado = 'resuelto'
       AND e.fila_id = b.id AND b.dest_dept_code IS NULL AND b.dest_dept = e.nombre;

    SELECT count(*) INTO v_sin_codigo
      FROM dependencia_codigo_backfill_doc WHERE resultado <> 'resuelto';

    RAISE NOTICE 'backfill dependencia por código (%): % pendientes, % radicados.dest_dept_code escritos, % ambiguos/huérfanos con evidencia',
        current_schema(), v_pendientes, v_resueltas, v_sin_codigo;
END $$;

COMMENT ON COLUMN borrador.dest_dept_code IS
    'Código de la dependencia destino (ADR-032). Llave de correlación; dest_dept es la instantánea del nombre.';
