-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E07, RF-DIG-01/RF-DIG-10): clasificación y foliación de anexos.
--
--   tipo_anexo   = código del catálogo de tipos de anexo (tenant-service, E14).
--   es_principal = marca el anexo principal del radicado.
--   folios       = número de folios del anexo (insumo del foliado del expediente).

ALTER TABLE anexos ADD COLUMN IF NOT EXISTS tipo_anexo   VARCHAR(50);
ALTER TABLE anexos ADD COLUMN IF NOT EXISTS es_principal BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE anexos ADD COLUMN IF NOT EXISTS folios       INTEGER;
