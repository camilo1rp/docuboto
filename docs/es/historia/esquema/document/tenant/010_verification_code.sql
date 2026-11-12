-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Migración de tenant (E13, F2): código de verificación para consulta pública.
--
-- Token no adivinable (distinto del número de radicado) que permite a un ciudadano
-- consultar SIN login la trazabilidad básica de su radicado. Se genera en la propia
-- BD (default) para no requerir cambios de aplicación ni en filas existentes.

ALTER TABLE radicados
    ADD COLUMN IF NOT EXISTS verification_code VARCHAR(32) NOT NULL
    DEFAULT replace(gen_random_uuid()::text, '-', '');

CREATE UNIQUE INDEX IF NOT EXISTS ux_radicados_verification_code
    ON radicados (verification_code);
