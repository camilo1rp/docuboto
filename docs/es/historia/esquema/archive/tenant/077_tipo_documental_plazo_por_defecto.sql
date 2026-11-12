-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant 077 (D2). search_path = tenant_{slug}.
--
-- Plazo por defecto del tipo, en días hábiles. Lo pone la entidad: no es un
-- término legal escrito en el código. NULL significa que una Entrada de este
-- tipo nace sin vencimiento si quien radica no indica días.

ALTER TABLE tipos_documentales
    ADD COLUMN IF NOT EXISTS default_response_days INT NULL
        CHECK (default_response_days BETWEEN 1 AND 3650);

COMMENT ON COLUMN tipos_documentales.default_response_days IS
    'Días hábiles de plazo cuando una Entrada de este tipo llega sin response_days. '
    'NULL: no hay plazo por defecto. Lo configura la entidad.';
