-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 007 (D-06): permisos granulares para solicitud de anulación y
-- respuesta vinculada.  Cierra el drift D-06: cualquier usuario autenticado podía
-- invocar estos endpoints sin verificación de permiso.
-- search_path = tenant_{slug} (ADR-012).

INSERT INTO auth_permissions (nombre, descripcion) VALUES
  ('PERM_SOL_ANULAR',          'Solicitar anulación de radicado (legado SGD_PANU_CODI crud=1)'),
  ('PERM_RESPUESTA_VINCULADA', 'Crear respuesta vinculada (Salida RE:, legado USUA_PERM_RESPUESTA)')
ON CONFLICT (nombre) DO NOTHING;
