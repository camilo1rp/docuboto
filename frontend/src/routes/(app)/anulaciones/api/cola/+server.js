// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /anulaciones/api/cola — cola de solicitudes pendientes de decidir (D-05).
 *
 * Existe además del `load` SSR porque la cola se recarga desde el cliente cada
 * vez que se aprueba o se rechaza una solicitud: sin este proxy, resolver una
 * anulación dejaría la fila resuelta en pantalla hasta recargar a mano, que es
 * exactamente lo que hace inútil una cola de trabajo.
 *
 * GET → { items: AnulacionPendiente[], total: number }
 *   `total` sale de `X-Total-Count`, que el backend recorta por clearance igual
 *   que las filas — el recuento no delata solicitudes que no se pueden ver.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { codigoError, mensajeError } from "$lib/utils/apiError.js";

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, url, fetch }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  const page = Number(url.searchParams.get("page") ?? 1);
  const size = Number(url.searchParams.get("size") ?? 20);
  const params = new URLSearchParams({
    page: String(Number.isFinite(page) && page >= 1 ? Math.trunc(page) : 1),
    size: String(Number.isFinite(size) && size >= 1 && size <= 100 ? Math.trunc(size) : 20),
  });

  try {
    const res = await fetch(`${API_V1}/anulaciones?${params.toString()}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": locals.user?.tenant_slug ?? "",
        "X-User-Id": locals.user?.sub ?? "",
      },
    });
    const cuerpo = await res.json().catch(() => null);
    if (!res.ok) {
      return json(
        {
          // `codigoError` y no `cuerpo.error`: el sobre de este proyecto va
          // ANIDADO (`detail: {error, detail, status}`) y leerlo plano es el
          // defecto que dejó un aviso diciendo «[object Object]». Un guardián
          // del repositorio caza exactamente esto.
          error: codigoError(cuerpo) ?? "upstream_error",
          detail: mensajeError(cuerpo, `Error ${res.status}`),
        },
        { status: res.status },
      );
    }
    return json({
      items: Array.isArray(cuerpo) ? cuerpo : [],
      total: Number(res.headers.get("X-Total-Count") ?? 0),
    });
  } catch {
    return json(
      { error: "upstream_unavailable", detail: "No se pudo consultar la cola de anulaciones." },
      { status: 502 },
    );
  }
}
