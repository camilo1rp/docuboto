-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 006 (E08): dependencia activa del usuario (contexto).
--
-- ADR-013: la autorización se resuelve en la BD por request; el cambio de
-- contexto NO reemite token, solo actualiza la dependencia activa del usuario.
-- `active_depe_id` = código de dependencia (tenant-service) actualmente activo;
-- debe corresponder a una URD activa del usuario.

ALTER TABLE auth_users
    ADD COLUMN IF NOT EXISTS active_depe_id INTEGER;
