// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /envios/+page.server.js — Carga SSR de la primera página de envíos postales (T-12e).
 *
 * Endpoint: GET /api/v1/envios?estado=&page=1&size=20
 *   - Paginación vía X-Total-Count (header de respuesta)
 *   - Filtro por estado (registrado|en_transito|entregado|devuelto|fallido)
 *   - Nuevo endpoint del gateway; se asume el contrato definido en T-12e mientras
 *     el backend lo implementa en paralelo.
 *
 * D-05: el access_token NO se expone en $page.data. Las llamadas client-side
 * usan /envios/api[/id/accion] que leen la cookie httpOnly en el servidor.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, url }) {
  const { user } = await parent();

  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";

  // Filtros iniciales de query params (deep-link friendly)
  const estado = url.searchParams.get("estado") ?? "";
  const page = Math.max(1, Number(url.searchParams.get("page") ?? 1));
  const size = 20;

  const params = new URLSearchParams({ page: String(page), size: String(size) });
  if (estado) params.set("estado", estado);

  /** @type {import('$lib/types/api.js').ShipmentResponse[]} */
  let shipments = [];
  let total = 0;
  let loadError = null;

  try {
    const res = await fetch(`${API_V1}/envios?${params.toString()}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });

    if (res.ok) {
      total = Number(res.headers.get("X-Total-Count") ?? 0);
      const data = await res.json();
      if (Array.isArray(data)) shipments = data;
    } else {
      const body = await res.json().catch(() => ({}));
      loadError = body.detail ?? `Error ${res.status}`;
    }
  } catch {
    loadError = "No se pudo conectar al servidor de envíos.";
  }

  return {
    shipments,
    total,
    loadError,
    initialFilters: { estado, page },
    // token y tenant NO se exponen: las llamadas cliente usan /envios/api (D-05)
  };
}
