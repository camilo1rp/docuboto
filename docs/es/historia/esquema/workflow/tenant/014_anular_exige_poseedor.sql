-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (ADR-028, remediación de auditoría): la transacción `anular`
-- exige tenencia.
--
-- `anular` cancela el paso activo (`target_step_status = 'cancelled'`) y, sin
-- `requires_holder`, cualquiera con PERM_ANULAR podía cancelar el paso de OTRA
-- persona —y, de paso, sondear radicados ajenos por el 409 `radicado_terminal`
-- (oráculo). El código ya exige tenencia a TODA transacción que mueve el paso;
-- esta fila deja el catálogo diciendo lo mismo que el código.
--
-- Nota de alcance: la anulación del RADICADO (acto con causal y aprobador) vive en
-- document-service y no pasa por aquí; esto es solo el motor genérico de trámite.
--
-- Idempotente. Rollback:
--   UPDATE transaction_types SET requires_holder = false WHERE code = 'anular';

UPDATE transaction_types
   SET requires_holder = true
 WHERE code = 'anular'
   AND requires_holder IS DISTINCT FROM true;
