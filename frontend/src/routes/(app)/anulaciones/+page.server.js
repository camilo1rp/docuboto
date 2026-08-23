// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * anulaciones/+page.server.js — Carga SSR de la cola del aprobador.
 *
 * **El hueco de backend que esta pantalla declaraba está cerrado.** Durante
 * toda la fase anterior, document-service exponía el estado de anulación POR
 * RADICADO (`GET /documents/{id}/anulacion`) y ningún endpoint que LISTARA lo
 * pendiente, así que la pantalla obligaba a buscar radicado por radicado y lo
 * explicaba en un banner permanente. Una cola de trabajo que obliga a adivinar
 * su propio contenido no es una cola: el aprobador solo se enteraba de que
 * tenía trabajo si alguien se lo decía por otro canal.
 *
 * Ahora existe `GET /api/v1/anulaciones` (recurso propio, gate
 * `PERM_PANU_CODI` —el mismo que decidir—, orden FIFO por `solicitado_at` y
 * no-read-up aplicado por igual a las filas y al recuento). La cola se carga
 * aquí en SSR; el refresco tras decidir va por `/anulaciones/api/cola`.
 *
 * El buscador NO se retira: sigue siendo la vía cuando alguien llega con un
 * número de radicado en la mano y quiere ir directo, sin recorrer la cola.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { mensajeError } from "$lib/utils/apiError.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, fetch }) {
  const { user } = await parent();
  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";

  /** @type {any[]} */
  let items = [];
  let total = 0;
  /** @type {{detail: string, status: number} | null} */
  let colaError = null;

  try {
    const res = await fetch(`${API_V1}/anulaciones?page=1&size=20`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": user?.tenant_slug ?? "",
      },
    });
    if (res.ok) {
      const cuerpo = await res.json();
      items = Array.isArray(cuerpo) ? cuerpo : [];
      total = Number(res.headers.get("X-Total-Count") ?? items.length);
    } else {
      const cuerpo = await res.json().catch(() => ({}));
      // Se conserva el ESTADO además del texto: «no tiene permiso» (403) y «el
      // servidor falló» (5xx) son dos cosas distintas para quien mira, y una
      // pantalla que las confunde acaba diciendo «no hay nada» cuando lo que
      // pasa es que no puede verlo.
      colaError = { detail: mensajeError(cuerpo, `Error ${res.status}`), status: res.status };
    }
  } catch {
    colaError = { detail: "No se pudo consultar la cola de anulaciones.", status: 0 };
  }

  return { cola: { items, total }, colaError };
}
