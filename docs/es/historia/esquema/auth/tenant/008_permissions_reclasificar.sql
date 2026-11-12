-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 008: permiso para reclasificar el nivel de seguridad de un radicado
-- (E08, RF-SEG-08). Cierra el drift D-XX: sin este permiso el endpoint
-- PATCH /documents/{id}/security-level quedaría sin gate granular.
-- search_path = tenant_{slug} (ADR-012).

INSERT INTO auth_permissions (nombre, descripcion) VALUES
  ('PERM_RECLASIFICAR', 'Reclasificar el nivel de seguridad de un radicado')
ON CONFLICT (nombre) DO NOTHING;
