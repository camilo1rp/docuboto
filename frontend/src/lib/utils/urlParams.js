// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Lectura de parámetros de la URL — y el default que sí se aplica.
 *
 * `Number(url.searchParams.get("stopk"))` parece razonable y no lo es: cuando
 * el parámetro falta, `get` devuelve `null` y **`Number(null)` es 0**, no
 * `NaN`. Un guardia posterior del estilo
 * `Number.isFinite(n) ? clamp(n) : porDefecto` toma entonces la rama buena
 * —0 es finito— y el clamp lo sube al mínimo. El valor por defecto queda
 * inalcanzable, y nadie lo nota porque el mínimo también es un número
 * plausible.
 *
 * Ocurrió así: entrar en la pestaña Semántica de `/busqueda` pedía **1**
 * resultado en vez de 5, y el desplegable «Cantidad de resultados» aparecía en
 * blanco porque 1 no es ninguna de sus opciones. Una búsqueda por significado
 * que devuelve un solo documento pierde justo lo que la hace útil: ofrecer
 * varios parecidos para que la persona elija.
 */

/**
 * Entero acotado leído de la URL.
 *
 * @param {URL} url
 * @param {string} nombre — nombre del parámetro
 * @param {{min: number, max: number, porDefecto: number}} rango
 * @returns {number}
 */
export function enteroDeLaUrl(url, nombre, { min, max, porDefecto }) {
  const crudo = url.searchParams.get(nombre);
  if (crudo === null || crudo.trim() === "") return porDefecto;
  const n = Number(crudo);
  if (!Number.isFinite(n)) return porDefecto;
  return Math.min(max, Math.max(min, Math.floor(n)));
}
