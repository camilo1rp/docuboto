// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * apiError.js — Leer el error de una respuesta del gateway, venga como venga.
 *
 * ## El sobre doble
 *
 * El formato de error del proyecto es `{error, detail, status}`. Pero los
 * routers lo levantan con `HTTPException(detail={...})`, y FastAPI serializa
 * **eso** dentro de su propio `detail`:
 *
 *     {"detail": {"error": "metadata_validation_failed", "detail": "…", "status": 422}}
 *
 * Los proxies BFF reenvían el cuerpo tal cual (`json(data, {status})`), así que
 * el sobre doble llega intacto al navegador. El frontend, en cambio, comparaba
 * `err.error` — el campo de PRIMER nivel, que no existe.
 *
 * Consecuencias medidas, no supuestas:
 *  - `HojaRuta` no reconocía `no_active_step` y un radicado recién radicado
 *    —sin flujo todavía, algo normal— decía «No se pudo cargar el paso actual»
 *    en vez de «Sin paso activo». El estado vacío correcto estaba escrito y no
 *    llegaba a pintarse nunca.
 *  - `/radicar` hacía `err?.detail ?? "Error al crear el radicado"` para el
 *    aviso: como `detail` es un OBJETO, el mensaje salía como
 *    «[object Object]», y el mapeo del error al campo concreto no se activaba.
 *
 * Estas funciones aceptan las dos formas a propósito: la plana existe de verdad
 * (errores que fabrica el propio proxy BFF, p. ej. `connection_error`) y la
 * anidada es la que produce el backend. Elegir una sola volvería a partir el
 * manejo de errores por la mitad.
 */

/**
 * Código de error (`error`) de un cuerpo de respuesta, plano o anidado.
 * @param {any} data
 * @returns {string|null}
 */
export function codigoError(data) {
  if (!data || typeof data !== "object") return null;
  if (typeof data.error === "string") return data.error;
  if (data.detail && typeof data.detail === "object" && typeof data.detail.error === "string") {
    return data.detail.error;
  }
  return null;
}

/**
 * Mensaje legible de un cuerpo de respuesta. **Nunca devuelve un objeto**: ese
 * es justo el fallo que motivó este módulo — un `[object Object]` en un aviso
 * de error deja a la persona sin saber qué pasó ni cómo arreglarlo.
 * @param {any} data
 * @param {string} porDefecto
 * @returns {string}
 */
export function mensajeError(data, porDefecto) {
  if (typeof data === "string" && data.trim()) return data;
  if (data && typeof data === "object") {
    if (typeof data.detail === "string" && data.detail.trim()) return data.detail;
    if (data.detail && typeof data.detail === "object" && typeof data.detail.detail === "string") {
      return data.detail.detail;
    }
    const codigo = codigoError(data);
    if (codigo) return codigo;
  }
  return porDefecto;
}
