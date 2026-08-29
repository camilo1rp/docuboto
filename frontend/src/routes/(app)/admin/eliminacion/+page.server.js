// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/eliminacion/+page.server.js — Los lotes de eliminación (E04, RF-TRD-07).
 *
 * La pantalla que faltaba: hasta ahora se podía PROPONER un lote desde la cola
 * de vencimientos y no había ningún sitio donde verlo después. Un
 * procedimiento que no se puede seguir es un procedimiento que se abandona a
 * medias — y el que termina aquí termina destruyendo.
 *
 * El detalle se carga en el SERVIDOR (`?lote=<id>`) y no por `fetch` del
 * cliente: el inventario se recorta por el clearance de quien mira
 * (RF-SEG-08), y ese recorte lo hace el backend con la identidad que viaja en
 * las cabeceras. Cargarlo aquí deja además el enlace compartible y el estado
 * en la URL.
 *
 * Gate real: `USUA_PERM_TRD` (`crud>=3` para publicar, avalar y anular) — el
 * mismo que gobierna la TRD, porque eliminar es ejecutar el instrumento.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { mensajeError } from "$lib/utils/apiError.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, fetch, url }) {
  const { user } = await parent();

  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": user?.tenant_slug ?? "",
  };
  if (user?.sub) headers["X-User-Id"] = user.sub;

  let lotes = [];
  let lotesError = null;
  // El ESTADO del error se conserva, no solo su texto: un 403 presentado como
  // conjunto vacío diría «no hay lotes» a quien lo que le falta es el permiso.
  let sinPermiso = false;
  try {
    const res = await fetch(`${API_V1}/eliminacion/lotes?size=100`, { headers });
    if (res.ok) {
      lotes = await res.json().catch(() => []);
    } else if (res.status === 403) {
      sinPermiso = true;
    } else {
      const body = await res.json().catch(() => ({}));
      lotesError = mensajeError(body, `Error ${res.status}`);
    }
  } catch {
    lotesError = "No se pudo conectar al servidor para consultar los lotes.";
  }

  const seleccionado = url.searchParams.get("lote") ?? "";
  let detalle = null;
  let detalleError = null;
  if (UUID_RE.test(seleccionado)) {
    try {
      const res = await fetch(`${API_V1}/eliminacion/lotes/${seleccionado}`, { headers });
      if (res.ok) {
        detalle = await res.json().catch(() => null);
      } else {
        const body = await res.json().catch(() => ({}));
        detalleError = mensajeError(body, `Error ${res.status}`);
      }
    } catch {
      detalleError = "No se pudo conectar al servidor para abrir el lote.";
    }
  }

  return { lotes, lotesError, sinPermiso, seleccionado, detalle, detalleError };
}
