-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Global migration 002: estado de aprovisionamiento para el patrón saga (E14).
-- Complementa la columna `status` con el ciclo de provisioning.

ALTER TABLE public.tenants
    ADD COLUMN IF NOT EXISTS provisioning_status VARCHAR(20) NOT NULL DEFAULT 'active';
    -- pending | active | provisioning_failed
