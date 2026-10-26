// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /anulaciones/api/buscar — Proxy SSR: localizador de radicados por texto,
 * usado como sustituto de la cola de anulaciones pendientes (D-05, ADR-011,
 * Fase 0 cierre desfase API↔UI).
 *
 * HUECO DE BACKEND: document-service no expone un listado de anulaciones
 * pendientes (ver `+page.server.js` de esta misma ruta para el detalle de la
 * investigación). Esta pantalla usa `GET /api/v1/search` (E09, ya existente)
 * para que el aprobador localice el radicado por número de radicado o texto
 * y luego consulte su estado de anulación uno por uno vía `/anulaciones/api/[id]`.
 *
 * Proxy PROPIO (no se reutiliza `/busqueda/api`): esta ruta tiene su propio
 * contrato de respuesta (`{ results, total }`, distinto del `{ items }` que
 * consume la pantalla /busqueda) y su propia validación de `q` — no porque
 * `/busqueda/api` esté roto (ya lee `data.hits` primero).
 *
 * GET → { results: SearchHit[], total: number }
 *   Parámetros: q (≥2 caracteres, obligatorio)
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

  const q = (url.searchParams.get("q") ?? "").trim();
  if (q.length < 2) {
    return json(
      { error: "validation_error", detail: "La búsqueda requiere al menos 2 caracteres." },
      { status: 400 },
    );
  }

  const params = new URLSearchParams({ q, page: "1", size: "20" });

  try {
    const res = await fetch(`${API_V1}/search?${params.toString()}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
        "X-User-Id": userId,
      },
    });

    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      return json(data, { status: res.status });
    }

    return json({ results: data.hits ?? [], total: data.total ?? 0 });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de búsqueda." },
      { status: 503 },
    );
  }
}
