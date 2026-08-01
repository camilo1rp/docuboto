// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/auditoria/api/verify — Proxy SSR: re-verificación bajo demanda de la
 * cadena de hash (`GET /audit/verify`, D-05). Recalcula sobre Postgres real
 * en cada llamada — no hay caché ni estado optimista: si la petición falla,
 * el cliente debe recibir un estado explícito de "no se pudo verificar"
 * (nunca inferir "íntegra" del silencio, ver +page.svelte).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals }) {
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
    const res = await fetch(`${API_V1}/audit/verify`, { headers });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      {
        error: "connection_error",
        detail: "No se pudo conectar al servidor para verificar la cadena.",
      },
      { status: 503 },
    );
  }
}
