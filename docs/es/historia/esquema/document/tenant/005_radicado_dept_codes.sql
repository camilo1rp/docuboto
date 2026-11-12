-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E01): vínculo del radicado con el organigrama.
--
-- origin_dept_code / dest_dept_code referencian el `codigo` (depe_codi) de una
-- dependencia del tenant (tenant-service, E14). document-service valida el
-- código vía HTTP y denormaliza el `nombre` de la dependencia en los campos de
-- texto origin_dept/dest_dept (snapshot legal del momento de radicación).
-- NULL = sin dependencia asociada (p. ej. remitente externo en una Entrada).

ALTER TABLE radicados
    ADD COLUMN IF NOT EXISTS origin_dept_code INTEGER;

ALTER TABLE radicados
    ADD COLUMN IF NOT EXISTS dest_dept_code INTEGER;

CREATE INDEX IF NOT EXISTS ix_radicados_dest_dept_code ON radicados (dest_dept_code);
CREATE INDEX IF NOT EXISTS ix_radicados_origin_dept_code ON radicados (origin_dept_code);
