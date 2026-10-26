// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/grupos/api/users-pickable — Proxy SSR: proyección mínima de usuarios
 * activos (D-05, D-10). GET → UserPickItem[] (id + username).
 *
 * Alimenta el buscador de "añadir miembro" del drawer de detalle de grupo.
 * Usa `GET /auth/users/pickable` (gateado con PERM_RADI en el backend, no
 * USUA_PERM_ADMIN) en vez de `GET /auth/users` — evita reenviar PII
 * administrativa (email, is_root) que esta pantalla no necesita.
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
  const headers = { Authorization: `Bearer ${token}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;

  try {
    // `size=500` es el tope real del backend (`le=500`). Se pedían 200, que no
    // era ni el máximo ni una decisión: con más usuarios activos, quien no
    // entrara en la ventana no existía para quien busca — y en un selector eso
    // no se lee como una lista corta, se lee como que esa persona no está en el
    // sistema. `X-Total-Count` viaja para que la pantalla pueda decirlo.
    const res = await fetch(`${API_V1}/auth/users/pickable?size=500`, { headers });
    const data = await res.json().catch(() => []);
    const total = res.headers.get("X-Total-Count");
    return json(data, {
      status: res.status,
      headers: total ? { "X-Total-Count": total } : {},
    });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo cargar el listado de usuarios." },
      { status: 503 },
    );
  }
}
