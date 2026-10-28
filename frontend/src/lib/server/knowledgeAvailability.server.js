// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * knowledgeAvailability.server.js — ¿Hay generación de texto para esta entidad?
 *
 * «Sugerir con antecedentes» (radicar y borradores) llama a `/knowledge/rag`. En
 * modo `local` la pasarela de conocimiento no genera (ADR-046 §3): `/rag`
 * responde 503 `rag_unavailable`. En vez de mostrar el botón y fallar, la carga
 * SSR pregunta antes a `GET /api/v1/knowledge/availability`, que responde sin
 * llamar al proveedor: `{ generation: boolean, mode: "local" | "ppi" }`.
 *
 * Fail-closed: solo `generation === true` estricto enciende el botón. Un 403
 * (entidad sin la bandera o persona sin `PERM_CONOCIMIENTO`), cualquier otro
 * error, un cuerpo inesperado o un fallo de red lo dejan apagado.
 *
 * No se pregunta si la sesión no puede usar conocimiento: el backend respondería
 * 403 y el resultado sería el mismo, con una petición de más en cada carga.
 */

import { API_V1 } from "$lib/utils/config.server.js";
import { puedeUsarConocimiento } from "$lib/utils/asistenteAcceso.js";

/**
 * @param {typeof fetch} fetchFn `fetch` del evento de carga.
 * @param {Record<string, string>} headers `Authorization` y `X-Tenant-Slug` del usuario.
 * @param {Parameters<typeof puedeUsarConocimiento>[0]} user sesión (bandera + permisos).
 * @returns {Promise<boolean>}
 */
export async function fetchGenerationAvailable(fetchFn, headers, user) {
  if (!puedeUsarConocimiento(user)) return false;
  try {
    const res = await fetchFn(`${API_V1}/knowledge/availability`, { headers });
    if (!res.ok) return false;
    const body = await res.json().catch(() => null);
    return body?.generation === true;
  } catch {
    return false;
  }
}
