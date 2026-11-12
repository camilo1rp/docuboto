-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 003: clasificación de seguridad (Ley 1712/2014) — E08.
-- Catálogo de niveles + matriz rol→nivel máximo accesible. El nivel concreto de
-- cada documento lo guarda document-service. search_path = tenant_{slug} (ADR-012).

CREATE TABLE IF NOT EXISTS security_levels (
    code    SMALLINT PRIMARY KEY,                     -- 1=Pública 2=Reservada 3=Clasificada
    nombre  VARCHAR(40) NOT NULL UNIQUE
);
INSERT INTO security_levels (code, nombre) VALUES
    (1, 'PUBLICA'), (2, 'RESERVADA'), (3, 'CLASIFICADA')
ON CONFLICT (code) DO NOTHING;

CREATE TABLE IF NOT EXISTS role_clearance (
    group_id   UUID PRIMARY KEY REFERENCES auth_groups(id) ON DELETE CASCADE,
    max_level  SMALLINT NOT NULL REFERENCES security_levels(code)
);
