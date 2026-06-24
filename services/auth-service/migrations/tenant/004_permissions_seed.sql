-- OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
-- Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
-- Contacto: aurigadl@gmail.com
--
-- Software libre: este archivo se distribuye bajo la Licencia Pública General
-- Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
-- conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
-- SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

-- Tenant migration 004: seed de permisos granulares (E08).
-- Los nombres de permiso son DATOS, nunca constantes en código (plan-icetex #15).
-- search_path = tenant_{slug} (ADR-012).

INSERT INTO auth_permissions (nombre, descripcion) VALUES
  ('USUA_PERM_ROOT',       'Bypass total de permisos'),
  ('PERM_RADI',            'Permiso base de radicación'),
  ('PERM_RADI_ENTRADA',    'Radicar documentos de Entrada'),
  ('PERM_RADI_SALIDA',     'Radicar documentos de Salida'),
  ('PERM_RADI_INTERNO',    'Radicar documentos Internos'),
  ('USUA_PERM_EXPEDIENTE', 'Gestión de expedientes'),
  ('PERM_TIPIF_ANEXO',     'Tipificar anexos'),
  ('PERM_DESCARGAEXP',     'Descargar expediente'),
  ('PERM_BORRAR_ANEXO',    'Borrar anexo'),
  ('PERM_FIRMA',           'Firma de documentos'),
  ('SGD_PERM_ESTADISTICA', 'Acceso a estadísticas'),
  ('USUA_PERM_TRD',        'Administración de TRD'),
  ('PERM_PANU_CODI',       'Anulación nivel jefe'),
  ('PERM_APRO_ANULAR',     'Aprobar anulación'),
  ('PERM_NRR',             'No requiere respuesta')
ON CONFLICT (nombre) DO NOTHING;
