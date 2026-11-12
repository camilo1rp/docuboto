-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- PERM_DUE_DATE: corregir el vencimiento de un radicado (nivel 3).
--
-- Va aparte de PERM_RADI. Quien tramita el radicado no debe poder ampliar su
-- propio plazo, y radicar no implica esa competencia. No se concede a ningún
-- grupo existente: el administrador lo reparte. La pantalla de grupos avisa
-- si nadie lo tiene.

INSERT INTO auth_permissions (nombre, descripcion) VALUES
  ('PERM_DUE_DATE',
   'Corregir el vencimiento de un radicado (nivel 3). Acortar se permite; ampliar se niega al asignado del paso y si la fecha pasa del doble del término inicial.')
ON CONFLICT (nombre) DO NOTHING;
