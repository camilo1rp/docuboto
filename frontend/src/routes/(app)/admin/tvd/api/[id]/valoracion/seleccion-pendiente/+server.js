// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/tvd/api/[id]/valoracion/seleccion-pendiente — Proxy SSR (D-05, C-5).
 *
 * GET → GET /tvd/{id}/valoracion/seleccion-pendiente
 * Expedientes de una valoración de Selección (S) que esperan el acto de selección: solo
 * identificador y código. El backend exige USUA_PERM_TRD >= 3.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params, url }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  if (!UUID_RE.test(params.id ?? "")) {
    return json({ error: "validation_error", detail: "Identificador inválido." }, { status: 400 });
  }
  const page = Math.max(1, Number.parseInt(url.searchParams.get("page") ?? "1", 10) || 1);
  const size = Math.min(
    200,
    Math.max(1, Number.parseInt(url.searchParams.get("size") ?? "50", 10) || 50),
  );
  try {
    const res = await fetch(
      `${API_V1}/tvd/${encodeURIComponent(params.id)}/valoracion/seleccion-pendiente?page=${page}&size=${size}`,
      {
        headers: {
          Authorization: `Bearer ${locals.accessToken}`,
          "X-Tenant-Slug": locals.user?.tenant_slug ?? "",
          ...(locals.user?.sub ? { "X-User-Id": locals.user.sub } : {}),
        },
      },
    );
    const cuerpo = await res.json().catch(() => ({}));
    return json(
      res.ok ? { items: cuerpo, total: Number(res.headers.get("X-Total-Count") ?? 0) } : cuerpo,
      { status: res.status },
    );
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo consultar la selección pendiente." },
      { status: 503 },
    );
  }
}
