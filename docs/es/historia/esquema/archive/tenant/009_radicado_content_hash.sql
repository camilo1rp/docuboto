-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E15 T-08, Acuerdo AGN 001/2024): valor huella por documento.
--
-- El SHA-256 del documento viaja con el vínculo (lo aporta quien vincula, que
-- conoce el contenido — storage/document-service o el usuario), evitando que
-- archive-service consulte internals de storage. El índice electrónico usa este
-- valor como `valor_huella` del item. NULL = huella pendiente.

ALTER TABLE expediente_radicados
    ADD COLUMN IF NOT EXISTS content_hash CHAR(64);
