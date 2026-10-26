// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/dependencias/api/tree — Proxy SSR: árbol jerárquico de dependencias
 * (D-05, E14). GET ?activo= → DependenciaNode[] (raíces con `children`
 * anidados). Alimenta la vista árbol de la página tras una acción de
 * crear/editar/desactivar (recarga vía `invalidateAll`/fetch cliente).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${token}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;

  const params = new URLSearchParams();
  const activo = url.searchParams.get("activo");
  if (activo === "true" || activo === "false") params.set("activo", activo);
  const qs = params.toString();

  try {
    const res = await fetch(`${API_V1}/dependencias/tree${qs ? `?${qs}` : ""}`, { headers });
    const data = await res.json().catch(() => []);
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo cargar el árbol de dependencias." },
      { status: 503 },
    );
  }
}
