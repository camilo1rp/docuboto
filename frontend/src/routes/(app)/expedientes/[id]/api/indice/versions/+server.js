// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /expedientes/[id]/api/indice/versions — Proxy SSR: historial de versiones
 * del índice electrónico (D-05, ADR-011, Fase 2, E15).
 *
 * GET /api/v1/expedientes/{id}/indice/versions?page&size → `IndiceVersionSummary[]`
 * (archive-service): versionado append-only del índice — cada cierre/rebuild
 * genera una versión nueva, la anterior queda inmutable.
 *
 * GET → historial paginado de versiones del índice.
 *   Parámetros: page (default 1), size (default 20, máx 100)
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  const { id } = params;
  if (!id || !UUID_RE.test(id)) {
    return json(
      { error: "validation_error", detail: "Identificador de expediente inválido." },
      { status: 400 },
    );
  }

  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  const rawPage = url.searchParams.get("page") ?? "1";
  const page = /^\d{1,5}$/.test(rawPage) ? Math.max(1, Number(rawPage)) : 1;
  const rawSize = url.searchParams.get("size") ?? "20";
  const size = Math.min(100, Math.max(1, /^\d{1,6}$/.test(rawSize) ? Number(rawSize) : 20));

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  const qs = new URLSearchParams({ page: String(page), size: String(size) });

  try {
    const res = await fetch(
      `${API_V1}/expedientes/${encodeURIComponent(id)}/indice/versions?${qs.toString()}`,
      { headers },
    );

    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      return json(data, { status: res.status });
    }

    const total = Number(
      res.headers.get("X-Total-Count") ?? (Array.isArray(data) ? data.length : 0),
    );
    return json({ items: data, total });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudieron obtener las versiones del índice." },
      { status: 503 },
    );
  }
}
