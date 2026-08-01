// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/grupos/api/security-levels — Proxy SSR: catálogo de niveles de
 * seguridad (D-05, E08, RF-SEG-08). GET → SecurityLevel[] (código + nombre).
 * Autenticado (sin gate de permiso extra en el backend, ver `clearance.py`).
 *
 * Alimenta el panel "Clearance del grupo" del drawer de detalle. El backend
 * NO expone lectura de la clearance YA asignada a un grupo (ver aviso en
 * +page.server.js) — este catálogo es solo el universo de niveles posibles.
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
    const res = await fetch(`${API_V1}/auth/security-levels`, { headers });
    const data = await res.json().catch(() => []);
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo cargar el catálogo de niveles de seguridad." },
      { status: 503 },
    );
  }
}
