-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E01, RF-RAD-04): fecha de vencimiento del radicado.
--
-- `due_date` es la fecha límite de respuesta, calculada en DÍAS HÁBILES desde
-- la radicación (festivos + fines de semana del tenant). El cálculo lo provee
-- tenant-service vía GET /api/v1/business-days/calculate; document-service sólo
-- persiste el resultado. NULL = radicado sin plazo de respuesta.

ALTER TABLE radicados
    ADD COLUMN IF NOT EXISTS due_date DATE;

CREATE INDEX IF NOT EXISTS ix_radicados_due_date ON radicados (due_date);
