-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 005: permiso de administración RBAC (E08, ADR-013).
-- Permite delegar la administración de usuarios/grupos/permisos sin ser ROOT.
-- search_path = tenant_{slug} (ADR-012).

INSERT INTO auth_permissions (nombre, descripcion) VALUES
  ('USUA_PERM_ADMIN', 'Administración de usuarios, grupos y permisos')
ON CONFLICT (nombre) DO NOTHING;
