-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E03, RF-MET-08): metadatos de disposición del documento.
--
-- Programa de eliminación, fechas de retención/acción/confirmación y marcas de
-- eliminación, derivados de la TRD de la serie. Se guardan como JSONB en el
-- propio radicado y se gestionan con endpoints dedicados (/disposition).

ALTER TABLE radicados
    ADD COLUMN IF NOT EXISTS disposition JSONB;
