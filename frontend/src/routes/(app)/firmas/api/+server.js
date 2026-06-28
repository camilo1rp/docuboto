// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /firmas/api — Proxy SSR para listar la bandeja del firmante con paginación (D-05, T-12d).
 *
 * El token se lee de locals.accessToken (hook); nunca se expone al cliente.
 *
 * GET ?page=&size= → { pendientes: SignaturePendingItem[]; total: number }
 *   - total viene del header X-Total-Count del gateway y/o del body.
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

  const page = url.searchParams.get("page") ?? "1";
  const size = url.searchParams.get("size") ?? "20";

  const params = new URLSearchParams({ page, size });

  try {
    const res = await fetch(`${API_V1}/signature/pending?${params}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });

    const headerTotal = res.headers.get("X-Total-Count");
    const data = await res.json().catch(() => ({}));

    if (!res.ok) return json(data, { status: res.status });

    const items = Array.isArray(data?.items) ? data.items : [];
    const total = headerTotal ? Number(headerTotal) : (data?.total ?? items.length);

    return json({ pendientes: items, total });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de firmas." },
      { status: 503 },
    );
  }
}
