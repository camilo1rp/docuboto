// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * busqueda/+page.server.js — Carga SSR de resultados de búsqueda avanzada (T-11, RF-UI-03).
 *
 * Endpoint real: GET /api/v1/search
 *   - q (≥2 chars, requerido), doc_type (E|S|I), status, date_from, date_to,
 *     dest_dept_code (int), page, size
 *   - Aplica clearance por X-User-Id (no read-up) — el proxy DEBE inyectarlo.
 *   - Paginación vía X-Total-Count y/o campo total en el body.
 *
 * PATRÓN (coherente con bandeja):
 *   - Esta carga SSR maneja el caso inicial y deep-links (URL bookmarkable).
 *   - Las búsquedas interactivas usan goto() para actualizar la URL, lo que
 *     vuelve a ejecutar este load con los nuevos params (sin recarga completa del
 *     navegador — SvelteKit navigation).
 *   - La paginación usa /busqueda/api (endpoint proxy local) para evitar la
 *     latencia del ciclo SSR completo, igual que bandeja/api hace con sus páginas.
 *
 * El access_token NO se expone en $page.data — nunca alcanza el JS del cliente.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, url }) {
  const { user } = await parent();

  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  // X-User-Id requerido por el endpoint de búsqueda para aplicar clearance (RF-SEG-08)
  const userId = user?.sub ?? "";

  // Leer filtros de la URL (deep-link friendly)
  const q = url.searchParams.get("q") ?? "";
  const docType = url.searchParams.get("doc_type") ?? "";
  const status = url.searchParams.get("status") ?? "";
  const dateFrom = url.searchParams.get("date_from") ?? "";
  const dateTo = url.searchParams.get("date_to") ?? "";
  // dest_dept_code: aceptar solo enteros positivos; descartar texto libre (422)
  const rawDeptCode = url.searchParams.get("dest_dept_code") ?? "";
  const destDeptCode =
    rawDeptCode !== "" && /^\d+$/.test(rawDeptCode.trim())
      ? String(Math.floor(Number(rawDeptCode)))
      : "";
  const page = Math.max(1, Number(url.searchParams.get("page") ?? 1));
  const size = 20;

  let results = [];
  let total = 0;
  let loadError = null;

  // Solo llamar al backend si hay al menos 2 caracteres de query
  if (q.length >= 2) {
    const params = new URLSearchParams({ q, page: String(page), size: String(size) });
    if (docType) params.set("doc_type", docType);
    if (status) params.set("status", status);
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);
    if (destDeptCode) params.set("dest_dept_code", destDeptCode);

    try {
      const res = await fetch(`${API_V1}/search?${params.toString()}`, {
        headers: {
          Authorization: `Bearer ${token}`,
          "X-Tenant-Slug": tenant,
          "X-User-Id": userId,
        },
      });

      if (res.ok) {
        const data = await res.json();
        // SearchResponse puede devolver { items, total } o un array con X-Total-Count
        if (Array.isArray(data)) {
          results = data;
          total = Number(res.headers.get("X-Total-Count") ?? data.length);
        } else {
          results = data.items ?? data.results ?? [];
          total = data.total ?? Number(res.headers.get("X-Total-Count") ?? 0);
        }
      } else {
        const body = await res.json().catch(() => ({}));
        loadError = body.detail ?? `Error ${res.status}`;
      }
    } catch {
      loadError = "No se pudo conectar al servidor de búsqueda.";
    }
  }

  return {
    results,
    total,
    loadError,
    // Filtros activos para inicializar el formulario en el cliente
    initialFilters: { q, docType, status, dateFrom, dateTo, destDeptCode, page },
    // token y user NO se exponen: las llamadas de paginación usan /busqueda/api (D-05)
  };
}
