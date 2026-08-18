// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/flujos/reglas/+page.server.js — Carga SSR de las reglas de
 * enrutamiento automático (E05).
 *
 * `GET /workflow/rules` no pagina con `X-Total-Count`: se pide una página
 * grande (200) y el resto (filtro por activa/inactiva) se resuelve en el
 * cliente con `DataTable` (`enableFilters`).
 *
 * Acceso: la vista es de administración (USUA_PERM_ADMIN, espejo RBAC); el
 * backend gatea `GET /workflow/rules` con `require_permission("USUA_PERM_ADMIN")`
 * y siempre re-valida (ver nota en `+page.svelte`).
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { mensajeError } from "$lib/utils/apiError.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, fetch }) {
  const { user } = await parent();

  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";

  let reglas = [];
  let loadError = null;

  try {
    const res = await fetch(`${API_V1}/workflow/rules?page=1&size=200`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });

    if (res.ok) {
      const raw = await res.json().catch(() => []);
      reglas = Array.isArray(raw) ? raw : [];
    } else {
      const body = await res.json().catch(() => ({}));
      loadError = mensajeError(body, `Error ${res.status}`);
    }
  } catch {
    loadError = "No se pudo conectar al servidor de reglas.";
  }

  return {
    reglas,
    loadError,
    // token y tenant NO se exponen: las escrituras del cliente usan
    // /admin/flujos/reglas/api/* (D-05).
  };
}
