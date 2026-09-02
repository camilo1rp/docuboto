// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /borradores/api/[id]/devolver — Proxy SSR para devolver un borrador aprobado a edición (D-05).
 *
 * POST → transición aprobado → borrador (E23, RF-PRO-10).
 *
 * Sin esta vuelta el ciclo era de una sola dirección y el texto quedaba
 * congelado al aprobar: la única salida de un aprobado era radicar. Con el
 * circuito de firmas abierto eso deja inalcanzable la mitad del trámite, porque
 * un circuito se revierte cuando el documento cambia — y no había forma de
 * cambiarlo.
 *
 * No toca el circuito: reabrir no invalida nada por sí mismo, y no debe. Lo que
 * tira las aprobaciones es EDITAR el texto, porque el disparador recalcula
 * `cuerpo_sha256` y la cadena deja de casar. La invalidación se deriva del
 * hecho, no del acto.
 *
 * El backend devuelve 409 si ya fue radicado o si no estaba aprobado.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { attachSafeCuerpo } from "$lib/server/sanitizeHtml.js";

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/borradores/${params.id}/devolver`, {
      method: "POST",
      headers,
    });

    const data = await res.json().catch(() => ({}));
    if (res.ok) attachSafeCuerpo(data);
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de borradores." },
      { status: 503 },
    );
  }
}
