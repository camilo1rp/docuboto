-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant 020 (B5, ronda de corrección, ADR-032). search_path = tenant_{slug}.
--
-- Completa la evidencia del backfill de la 019:
--   * `antes` / `despues` (jsonb, SIN truncar): la regla `conditions` antes y después
--     de la reescritura por código. La 019 solo dejó `left(conditions::text, 2000)`
--     en `nombre`, que mutila las reglas largas.
--   * `historico_id`: la fila de `dependencia_nombre_historico` que decidió una
--     resolución 'mapeado' (decisión humana enlazada, no solo marcada).
--   * `reconstruido`: TRUE cuando `antes`/`despues` se dedujeron DESPUÉS del hecho
--     (filas escritas por la 019): `antes` se parsea del texto guardado solo si es
--     JSON completo (< 2000 caracteres); `despues` es la regla vigente solo si ya
--     no contiene condiciones por nombre eq/ne/in (consistente con la reescritura).
--     Lo que no se puede probar queda NULL: no se inventa evidencia.
--
-- La evidencia es de solo inserción: el trigger se aparta DENTRO de la transacción
-- solo para completar columnas NUEVAS (NULL -> valor) y se verifica que quedó
-- habilitado. Idempotente (IS NULL): la 2.ª ejecución no cambia filas.
-- Rollback: DROP COLUMN de las tres columnas.

ALTER TABLE dependencia_codigo_backfill
    ADD COLUMN IF NOT EXISTS antes        JSONB,
    ADD COLUMN IF NOT EXISTS despues      JSONB,
    ADD COLUMN IF NOT EXISTS historico_id BIGINT,
    ADD COLUMN IF NOT EXISTS reconstruido BOOLEAN NOT NULL DEFAULT FALSE;

-- jsonb tolerante: NULL si el texto no es JSON válido (p. ej. truncado).
CREATE OR REPLACE FUNCTION _b5_jsonb_o_null(p text) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path FROM CURRENT AS $$
BEGIN
    RETURN p::jsonb;
EXCEPTION WHEN others THEN
    RETURN NULL;
END
$$;

DO $$
DECLARE
    v_pend BOOLEAN;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM dependencia_codigo_backfill
         WHERE (columna = 'conditions:code' AND antes IS NULL AND despues IS NULL AND NOT reconstruido)
            OR (resultado = 'mapeado' AND historico_id IS NULL)
    ) INTO v_pend;
    IF NOT v_pend THEN
        RETURN;
    END IF;

    ALTER TABLE dependencia_codigo_backfill DISABLE TRIGGER trg_dependencia_codigo_backfill_immutable;

    UPDATE dependencia_codigo_backfill e
       SET antes = CASE WHEN char_length(e.nombre) < 2000 THEN _b5_jsonb_o_null(e.nombre) END,
           despues = (SELECT w.conditions FROM workflow_rules w
                       WHERE w.id = e.fila_id
                         AND jsonb_typeof(w.conditions) = 'array'
                         AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(w.conditions) c
                                          WHERE c->>'field' IN ('dest_dept', 'origin_dept')
                                            AND c->>'op' IN ('eq', 'ne', 'in'))),
           reconstruido = TRUE
     WHERE e.columna = 'conditions:code' AND e.antes IS NULL AND e.despues IS NULL
       AND NOT e.reconstruido;

    IF to_regclass(format('%I.dependencia_nombre_historico', current_schema())) IS NOT NULL THEN
        UPDATE dependencia_codigo_backfill e
           SET historico_id = h.id
          FROM (SELECT DISTINCT ON (nombre, codigo) id, nombre, codigo
                  FROM dependencia_nombre_historico WHERE origen = 'mapeo_manual'
                 ORDER BY nombre, codigo, id DESC) h
         WHERE e.resultado = 'mapeado' AND e.historico_id IS NULL
           AND h.nombre = e.nombre AND h.codigo = e.codigo;
    END IF;

    ALTER TABLE dependencia_codigo_backfill ENABLE TRIGGER trg_dependencia_codigo_backfill_immutable;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger
                    WHERE tgrelid = 'dependencia_codigo_backfill'::regclass
                      AND tgname = 'trg_dependencia_codigo_backfill_immutable' AND tgenabled = 'O') THEN
        RAISE EXCEPTION 'trg_dependencia_codigo_backfill_immutable no quedó habilitado';
    END IF;
END $$;
