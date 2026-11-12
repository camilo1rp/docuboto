-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E02, F3, ADR-015): disposición del expediente al cierre.
--
-- Al cerrar un expediente con serie TRD asignada se materializa su calendario de
-- retención/disposición (calculado de la serie, E04) en esta columna, para que el
-- ciclo de vida quede registrado en el propio expediente.

ALTER TABLE expedientes
    ADD COLUMN IF NOT EXISTS disposition JSONB;
