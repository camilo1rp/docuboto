-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Término inicial del radicado: la primera fecha de vencimiento.
--
-- `due_date` se corrige después. Sin esta columna, el tope «doble del término
-- inicial» se mediría contra la fecha ya corregida y cada ampliación lo
-- empujaría. Se fija en el alta y la corrección no lo mueve. Lo que ya tenía
-- vencimiento toma ese vencimiento como término inicial.

ALTER TABLE radicados
    ADD COLUMN IF NOT EXISTS termino_inicial DATE;

UPDATE radicados
   SET termino_inicial = due_date
 WHERE termino_inicial IS NULL
   AND due_date IS NOT NULL;

COMMENT ON COLUMN radicados.termino_inicial IS
    'Primera fecha de vencimiento. La corrección actualiza due_date y deja esta.';
