-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- ADR-028 (remediación de auditoría): el estado del radicado es de un conjunto
-- CERRADO. Hasta ahora `radicados.status` era un VARCHAR(20) libre: cualquier
-- valor cabía, incluido uno que ninguna máquina de estados conoce (un paquete
-- importado, un UPDATE a mano). Los guardas del flujo (`closed`/`anulado` son
-- terminales) solo valen si el dominio de valores está cerrado.
--
-- Valores admitidos: los que el código escribe — `registered` (alta),
-- `distributed`/`in_progress` (los mueve el workflow-service), `closed` (acción
-- «Finalizar trámite») y `anulado` (la anulación aprobada). Verificado contra el
-- código y los seeds: ningún otro literal se escribe en `radicados.status`.
--
-- Se añade NOT VALID (rige para lo que se escriba desde ya) y se intenta VALIDAR lo
-- existente: si alguna fila trae un valor fuera del conjunto (datos heredados), la
-- migración NO falla —tumbaría el arranque de la entidad— y deja el aviso con el
-- recuento; la restricción sigue protegiendo toda escritura nueva y se valida a
-- mano (`ALTER TABLE radicados VALIDATE CONSTRAINT radicados_status_check`) tras
-- corregir esas filas.
--
-- Idempotente. Rollback:
--   ALTER TABLE radicados DROP CONSTRAINT IF EXISTS radicados_status_check;

DO $$
DECLARE
    v_fuera INTEGER;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'radicados_status_check'
           AND conrelid = format('%I.radicados', current_schema())::regclass
    ) THEN
        ALTER TABLE radicados
            ADD CONSTRAINT radicados_status_check
            CHECK (status IN ('registered', 'distributed', 'in_progress', 'closed', 'anulado'))
            NOT VALID;
    END IF;

    SELECT count(*) INTO v_fuera FROM radicados
     WHERE status NOT IN ('registered', 'distributed', 'in_progress', 'closed', 'anulado');
    IF v_fuera = 0 THEN
        ALTER TABLE radicados VALIDATE CONSTRAINT radicados_status_check;
    ELSE
        RAISE WARNING 'radicados_status_check (%): % filas con un status fuera del conjunto; '
                      'la restricción queda SIN VALIDAR (rige solo para escrituras nuevas)',
                      current_schema(), v_fuera;
    END IF;
END $$;
