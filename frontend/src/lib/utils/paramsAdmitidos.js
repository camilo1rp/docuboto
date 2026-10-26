// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Claves que `PUT /config/params/{key}` admite (allowlist del backend, 422
 * `config_param_desconocido` para cualquier otra). Fuente única en
 * `services/tenant-service/app/routers/config.py::PARAMS_ADMITIDOS`; el test
 * `parametros-admitidos-paridad.test.js` las cruza.
 */
export const PARAMS_ADMITIDOS = [
  "entidad_nombre",
  "entidad_sigla",
  "entidad_nit",
  "entidad_direccion",
  "entidad_telefono",
  "entidad_email",
  "entidad_web",
  "firma_personal_require_xades",
  "politica_tratamiento_version",
  "politica_tratamiento_url",
  "politica_tratamiento_texto",
];

/** Claves admitidas que aún no existen en el tenant (las que se pueden «crear»). @param {string[]} existentes */
export function clavesPorCrear(existentes) {
  const ya = new Set(existentes);
  return PARAMS_ADMITIDOS.filter((k) => !ya.has(k));
}
