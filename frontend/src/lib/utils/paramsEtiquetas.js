// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Etiqueta legible de cada parámetro configurable (selector de «Nuevo
 * parámetro»). La lista de claves admitidas la fija el backend
 * (`paramsAdmitidos.js`); aquí solo se les pone nombre. Una clave sin entrada
 * se muestra tal cual: faltar una etiqueta no rompe nada.
 * @type {Record<string, string>}
 */
export const ETIQUETA_PARAM = {
  entidad_nombre: "Nombre de la entidad",
  entidad_sigla: "Sigla de la entidad",
  entidad_nit: "NIT de la entidad",
  entidad_direccion: "Dirección de la entidad",
  entidad_telefono: "Teléfono de la entidad",
  entidad_email: "Correo de la entidad",
  entidad_web: "Sitio web de la entidad",
  firma_personal_require_xades: "Firma personal: exigir XAdES",
  politica_tratamiento_version: "Política de tratamiento de datos: versión",
  politica_tratamiento_url: "Política de tratamiento de datos: enlace",
  politica_tratamiento_texto: "Política de tratamiento de datos: texto",
  formato_numero_radicado: "Formato del número de radicado",
  expediente_number_format: "Formato del número de expediente",
  notificaciones_mail: "Notificaciones por correo",
  firma_digital_provider: "Proveedor de firma digital",
};

/** «Etiqueta (clave)» para el selector; la clave sola si no hay etiqueta. @param {string} k */
export function etiquetaDeParam(k) {
  const e = ETIQUETA_PARAM[k];
  return e ? `${e} (${k})` : k;
}
