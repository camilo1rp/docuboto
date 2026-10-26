// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /firmas/+page.server.js — Carga SSR de la bandeja del firmante (T-12d, RF-UI-17).
 *
 * Endpoint: GET /api/v1/signature/pending?page=1&size=20
 *   - Bandeja cross-documento del firmante: solicitudes cuyo turno activo
 *     es del usuario actual (firmante del JWT).
 *   - Paginación: X-Total-Count (header) + body {items, total, page, size}.
 *   - Permiso requerido en el backend: PERM_FIRMA.
 *
 * D-05: el access_token NO se expone en $page.data. Las llamadas client-side
 * usan /firmas/api[/id/accion] que leen locals.accessToken en el servidor.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { mensajeError } from "$lib/utils/apiError.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, url }) {
  const { user } = await parent();

  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";

  // Filtro de paginación (deep-link friendly)
  const page = Math.max(1, Number(url.searchParams.get("page") ?? 1));
  const size = 20;

  const params = new URLSearchParams({ page: String(page), size: String(size) });

  /** @type {import('$lib/types/api.js').SignaturePendingItem[]} */
  let pendientes = [];
  let total = 0;
  let loadError = null;

  try {
    const res = await fetch(`${API_V1}/signature/pending?${params.toString()}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });

    if (res.ok) {
      // El total viene del header X-Total-Count y/o del body
      const headerTotal = res.headers.get("X-Total-Count");
      const data = await res.json();
      if (data?.items && Array.isArray(data.items)) {
        pendientes = data.items;
        total = headerTotal ? Number(headerTotal) : (data.total ?? pendientes.length);
      }
    } else {
      const body = await res.json().catch(() => ({}));
      loadError = mensajeError(body, `Error ${res.status}`);
    }
  } catch {
    loadError = "No se pudo conectar al servidor de firmas.";
  }

  return {
    pendientes,
    total,
    loadError,
    initialFilters: { page },
    // token y tenant NO se exponen: las llamadas cliente usan /firmas/api (D-05)
  };
}
