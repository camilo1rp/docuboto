// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * POST /admin/inteligencia/api/retiro — Proxy de POST /config/intelligence/withdraw.
 *
 * No lleva cuerpo: el acto borra todo lo de la entidad y no cambia el proveedor.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${locals.accessToken}`,
    "X-Tenant-Slug": locals.user?.tenant_slug ?? "",
  };
  if (locals.user?.sub) headers["X-User-Id"] = locals.user.sub;

  try {
    const res = await fetch(`${API_V1}/config/intelligence/withdraw`, {
      method: "POST",
      headers,
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo pedir el retiro de los datos." },
      { status: 503 },
    );
  }
}
