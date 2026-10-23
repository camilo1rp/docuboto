// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /archivo-fisico/api/unidades/[id]/prestatarios-elegibles — Proxy SSR (D-05, C11).
 *
 * GET ?q=&page=&size= → GET /unidades/{id}/prestatarios-elegibles
 * Personas activas cuyo acceso cubre el contenido de la unidad: la UI espeja el
 * gate del backend y no ofrece a quien el backend rechazará. Devuelve
 * `{user_id, username, nombre}`; nunca el nivel de acceso.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

export async function GET({ locals, params, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const q = new URLSearchParams();
  const texto = (url.searchParams.get("q") ?? "").trim().slice(0, 100);
  if (texto) q.set("q", texto);
  const page = url.searchParams.get("page");
  q.set("page", page && /^\d+$/.test(page) ? page : "1");
  const size = url.searchParams.get("size");
  q.set("size", size && /^\d+$/.test(size) ? size : "20");
  try {
    const res = await fetch(
      `${API_V1}/unidades/${encodeURIComponent(params.id)}/prestatarios-elegibles?${q.toString()}`,
      { headers: { Authorization: `Bearer ${token}`, "X-Tenant-Slug": tenant } },
    );
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo consultar a las personas elegibles." },
      { status: 503 },
    );
  }
}
