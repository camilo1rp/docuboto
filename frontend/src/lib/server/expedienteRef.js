// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Resolver un expediente por lo que la persona tiene delante: su CÓDIGO.
 *
 * Las pantallas pedían el UUID («UUID del expediente»), y ninguna vista del
 * sistema muestra ese identificador ni deja copiarlo: para vincular un
 * expediente a una caja había que sacarlo de la base de datos. El código
 * (`EXP-2026-0020`) sí está impreso en la carátula, en el listado y en la
 * ficha — es la identidad con la que se trabaja.
 *
 * El UUID se sigue aceptando: quien lo tenga (una integración, un enlace
 * profundo) no se queda fuera, y además se ahorra la búsqueda.
 *
 * La coincidencia es EXACTA. La búsqueda es de texto completo y devuelve
 * parecidos por definición; vincular un expediente a una unidad física, o
 * decir dónde está, son actos sobre un expediente concreto — el más parecido
 * no vale.
 */

import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * @param {string} referencia — UUID o código de expediente
 * @param {string} token
 * @param {string} tenant
 * @returns {Promise<string|null>} UUID del expediente, o null si no hay una única coincidencia exacta
 */
export async function resolverExpediente(referencia, token, tenant) {
  const ref = (referencia ?? "").trim();
  if (!ref) return null;
  if (UUID_RE.test(ref)) return ref;

  let res;
  try {
    res = await fetch(`${API_V1}/expedientes/search?q=${encodeURIComponent(ref)}&size=20`, {
      headers: { Authorization: `Bearer ${token}`, "X-Tenant-Slug": tenant },
    });
  } catch {
    return null;
  }
  if (!res.ok) return null;

  const cuerpo = await res.json().catch(() => null);
  const hits = Array.isArray(cuerpo?.hits) ? cuerpo.hits : [];
  const exactos = hits.filter((h) => String(h?.code ?? "").trim() === ref);
  return exactos.length === 1 ? String(exactos[0].id) : null;
}
