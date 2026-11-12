-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 055 (E12, AREA-07-09): `transferencias.objeto` obligatorio EN LA BASE.
-- search_path = tenant_{slug} (ADR-012).
--
-- La migración 052 dejó `objeto` sin NOT NULL porque las transferencias anteriores no lo
-- tienen y no hay valor honesto con el que rellenarlas; la obligatoriedad se aplicaba solo
-- en la ENTRADA (`TransferenciaCreate`). Cualquier INSERT que no pase por esa clase (un
-- script, otra ruta futura) podía crear una transferencia sin objeto, y el acta del Anexo
-- FUID saldría con la cabecera en blanco.
--
-- Un CHECK ... NOT VALID obliga a las filas NUEVAS sin tocar el histórico. Pero NOT VALID
-- también se evalúa en cada UPDATE de una fila existente, y `enviar`/`recibir`/`rechazar`
-- actualizan la fila: un `CHECK (objeto IS NOT NULL ...)` a secas dejaría SIN poder
-- avanzar a toda transferencia en curso anterior a la 052. Por eso la condición exime las
-- filas creadas antes de esta migración: el histórico queda intacto Y operable, y toda
-- fila posterior exige objeto.
--
-- Idempotente (guard acotado a `current_schema()`).

DO $$
BEGIN
    IF to_regclass(format('%I.transferencias', current_schema())) IS NULL THEN
        RETURN;
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'ck_transferencias_objeto'
           AND conrelid = to_regclass(format('%I.transferencias', current_schema()))
    ) THEN
        ALTER TABLE transferencias
            ADD CONSTRAINT ck_transferencias_objeto
            CHECK (
                created_at < TIMESTAMPTZ '2026-10-03 00:00:00+00'
                OR (objeto IS NOT NULL AND length(btrim(objeto)) >= 3)
            ) NOT VALID;
    END IF;
END;
$$;

-- Rollback manual (no automatizado):
-- ALTER TABLE transferencias DROP CONSTRAINT IF EXISTS ck_transferencias_objeto;
