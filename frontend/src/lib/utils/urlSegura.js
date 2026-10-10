// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Devuelve la URL solo si es http(s) absoluta; si no, cadena vacía (sin enlace).
 * Defensa en profundidad: impide `javascript:`, `data:` y similares en un `href`
 * aunque el backend ya los rechace.
 * @param {unknown} valor
 * @returns {string}
 */
export function urlHttpSegura(valor) {
  if (typeof valor !== "string") return "";
  const t = valor.trim();
  if (!/^https?:\/\//i.test(t)) return "";
  try {
    const u = new URL(t);
    return u.protocol === "http:" || u.protocol === "https:" ? t : "";
  } catch {
    return "";
  }
}
