// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * reportes/+page.server.js — Carga SSR del resumen de reportes de radicados (T-11).
 *
 * Endpoint real: GET /api/v1/reports/radicados
 *   - Parámetros opcionales: date_from, date_to (ISO date)
 *   - Devuelve: { by_type, by_status, by_month, by_dept } con conteos.
 *
 * La descarga CSV usa el endpoint local /reportes/api/csv (proxy SSR),
 * no esta carga SSR — el botón apunta a ese endpoint con los mismos params.
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
  // X-User-Id: inyectar para que quede activo cuando el backend de reportes aplique clearance
  const userId = user?.sub ?? "";

  // Filtros de rango de fechas (deep-link friendly)
  const dateFrom = url.searchParams.get("date_from") ?? "";
  const dateTo = url.searchParams.get("date_to") ?? "";

  const params = new URLSearchParams();
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);
  const qs = params.toString();

  let summary = null;
  let loadError = null;

  try {
    const res = await fetch(`${API_V1}/reports/radicados${qs ? `?${qs}` : ""}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
        "X-User-Id": userId,
      },
    });

    if (res.ok) {
      summary = await res.json();
    } else {
      const body = await res.json().catch(() => ({}));
      loadError = body.detail ?? `Error ${res.status}`;
    }
  } catch {
    loadError = "No se pudo conectar al servidor de reportes.";
  }

  return {
    summary,
    loadError,
    // Filtros activos para inicializar el formulario y construir la URL del CSV
    initialFilters: { dateFrom, dateTo },
    // token NO se expone: el botón CSV llama a /reportes/api/csv (D-05)
  };
}
