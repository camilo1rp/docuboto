-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 009 (ADR-021 Fase 5): permiso para inspeccionar y reprocesar
-- el dead-letter de eventos (replay/discard). Gate granular de los endpoints
-- admin/deadletter en signature-service y workflow-service.
-- search_path = tenant_{slug} (ADR-012).

INSERT INTO auth_permissions (nombre, descripcion) VALUES
  ('PERM_DLQ_ADMIN', 'Inspeccionar y reprocesar el dead-letter de eventos')
ON CONFLICT (nombre) DO NOTHING;
