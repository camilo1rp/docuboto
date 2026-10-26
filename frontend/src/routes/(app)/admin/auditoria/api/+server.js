// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/auditoria/api — Proxy SSR: listado filtrado del registro de
 * auditoría (`GET /audit`, `services/auth-service/app/routers/audit.py`,
 * D-05). Filtros soportados por el backend: action, object_type, object_ref,
 * actor (todos igualdad exacta, ver `_FILTERABLE` en
 * `AuditQueryRepository`); page/size con `X-Total-Count` en la respuesta —
 * se reenvía intacta para que la tabla muestre la paginación real.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const FILTERABLE = ["action", "object_type", "object_ref", "actor"];

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  const params = new URLSearchParams();
  for (const key of FILTERABLE) {
    const value = url.searchParams.get(key);
    if (value) params.set(key, value);
  }
  params.set("page", url.searchParams.get("page") ?? "1");
  params.set("size", url.searchParams.get("size") ?? "20");

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/audit?${params}`, { headers });
    const data = await res.json().catch(() => []);
    return json(data, {
      status: res.status,
      headers: { "X-Total-Count": res.headers.get("X-Total-Count") ?? "0" },
    });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de auditoría." },
      { status: 503 },
    );
  }
}
