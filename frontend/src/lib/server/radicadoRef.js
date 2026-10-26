// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Resuelve un NÚMERO DE RADICADO al UUID que la API necesita.
 *
 * Hermano de `expedienteRef.js`, y por el mismo motivo: el número de radicado
 * (`2026-DEMO-E-000017`) es la identidad oficial del documento —la que está
 * impresa en el sello, la que cita el ciudadano, la que exige el Acuerdo AGN
 * 060/2001— y el UUID no se muestra en ninguna pantalla del sistema. Un campo
 * que pide el UUID no se puede rellenar sin abrir la base de datos.
 *
 * El caso que lo motivó: la carga masiva de expedientes ofrecía «Números de
 * radicado a vincular» y mandaba esos números en `tracking_numbers`. Pero
 * archive-service vincula por `radicado_ids`, y `tracking_numbers` es solo una
 * lista de etiquetas PARALELA a aquella (E15 perfil v4). Sin `radicado_ids`, la
 * lista de números no vinculaba nada — y el lote informaba «Completado, 0 con
 * error» sobre un expediente vacío. El docstring del proxy afirmaba que el
 * backend «resuelve ambos»; no lo hace, y esa frase era parte del defecto.
 *
 * Se resuelve con `GET /documents/by-tracking/{numero}`, que además aplica el
 * no-read-up: un radicado por encima del clearance de quien opera responde 404
 * y aquí se traduce a «no se pudo resolver», sin revelar que existe.
 */

import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * @param {string} referencia — UUID o número de radicado
 * @param {string} token
 * @param {string} tenant
 * @returns {Promise<string|null>} UUID del radicado, o null si no se resuelve
 */
export async function resolverRadicado(referencia, token, tenant) {
  const ref = (referencia ?? "").trim();
  if (!ref) return null;
  if (UUID_RE.test(ref)) return ref;

  let res;
  try {
    res = await fetch(`${API_V1}/documents/by-tracking/${encodeURIComponent(ref)}`, {
      headers: { Authorization: `Bearer ${token}`, "X-Tenant-Slug": tenant },
    });
  } catch {
    return null;
  }
  if (!res.ok) return null;

  const cuerpo = await res.json().catch(() => null);
  const id = cuerpo?.id;
  return typeof id === "string" && UUID_RE.test(id) ? id : null;
}

/**
 * Resuelve una lista, conservando el orden y diciendo cuáles no se pudieron.
 *
 * El orden importa: `radicado_ids` y `tracking_numbers` viajan al backend como
 * listas PARALELAS por índice, y desalinearlas etiquetaría cada radicado con el
 * número del siguiente.
 *
 * @param {string[]} referencias
 * @param {string} token
 * @param {string} tenant
 * @returns {Promise<{ids: string[], numeros: string[], noResueltos: string[]}>}
 */
export async function resolverRadicados(referencias, token, tenant) {
  const lista = Array.isArray(referencias) ? referencias : [];
  const ids = [];
  const numeros = [];
  const noResueltos = [];
  for (const ref of lista) {
    const id = await resolverRadicado(ref, token, tenant);
    if (id) {
      ids.push(id);
      numeros.push(String(ref).trim());
    } else {
      noResueltos.push(String(ref).trim());
    }
  }
  return { ids, numeros, noResueltos };
}
