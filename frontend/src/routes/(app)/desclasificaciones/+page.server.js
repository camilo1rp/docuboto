// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * desclasificaciones/+page.server.js — Carga SSR de la cola de la SEGUNDA
 * persona (C-2, ADR-039).
 *
 * `GET /api/v1/desclasificaciones` (gate `PERM_RECLASIFICAR` crud>=4), orden FIFO,
 * con el recorte de clearance aplicado por igual a las filas y a `X-Total-Count`.
 * La fila marca `propia` las solicitudes del propio llamante: quien solicitó no
 * puede decidirlas. El refresco tras decidir va por `/desclasificaciones/api/cola`.
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
    const res = await fetch(`${API_V1}/desclasificaciones?page=1&size=20`, {
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
      // Se conserva el ESTADO: «no tiene permiso» (403) y «el servidor falló» (5xx)
      // son cosas distintas, y confundirlas acaba diciendo «no hay nada».
      colaError = { detail: mensajeError(cuerpo, `Error ${res.status}`), status: res.status };
    }
  } catch {
    colaError = { detail: "No se pudo consultar la cola de desclasificaciones.", status: 0 };
  }

  return { cola: { items, total }, colaError };
}
