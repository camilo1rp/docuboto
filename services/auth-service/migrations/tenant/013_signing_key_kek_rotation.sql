-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E17/F4 -- rotación de la KEK de custodia): soporte al
-- barrido del re-wrap y a la verificación de retirada de una KEK.
--
-- La columna `key_version` YA existe (migración 011, DEFAULT 1). Esta migración
-- SOLO añade un índice: durante una rotación la inmensa mayoría de filas ya
-- está en la versión activa, así que `WHERE key_version <> activa` (barrido) y
-- `GROUP BY key_version` (conteo de retirada, cross-tenant) se benefician de él.
-- No cambia datos ni el esquema lógico; el tracking de la rotación son los
-- asientos `signing_key.rewrapped` (uno por credencial) en `audit_log` + este
-- conteo por versión, sin tabla nueva.

CREATE INDEX IF NOT EXISTS ix_auth_user_signing_key_key_version
    ON auth_user_signing_key (key_version);

-- Rollback: DROP INDEX IF EXISTS ix_auth_user_signing_key_key_version;
