// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Forma REAL de una plantilla de `GET /metadata/templates`
 * (`MetadataTemplateResponse`, document-service). Los tests construyen sus
 * mocks con esta función en vez de escribir el literal a mano: un mock que
 * inventa la clave (`schema` en vez de `json_schema`) dejaba la pantalla
 * vacía con la suite en verde. `plantillaMetadatos.contrato.test.js` compara
 * las claves de aquí con el modelo Pydantic.
 *
 * @param {{json_schema: Record<string, any>} & Record<string, any>} extra
 */
export function plantillaMetadatos(extra) {
  return {
    id: "00000000-0000-4000-8000-000000000001",
    tipo_documental: "OFI",
    version: 1,
    activo: true,
    created_at: "2026-10-01T10:00:00Z",
    ...extra,
  };
}
