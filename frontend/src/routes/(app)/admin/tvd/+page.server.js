// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/tvd/+page.server.js — Carga SSR de agrupaciones de valoración TVD
 * (ADR-026: Tablas de Valoración Documental, fondo acumulado).
 *
 * GET /tvd (paginado, X-Total-Count) — agrupaciones jerárquicas
 * (sección→subsección→serie del fondo, `parent_id`, ADR-026 D2). Se pide
 * `size=200` para traer el árbol completo de una vez, mismo motivo que
 * admin/trd: la vista construye el árbol en cliente y una paginación
 * parcial rompería la jerarquía.
 *
 * RBAC real: el backend gatea creación/edición/circuito con USUA_PERM_TRD
 * -- ADR-026 reutiliza a propósito el permiso de TRD (misma potestad
 * archivística, mismo Comité; ver services/archive-service/app/routers/
 * tvd.py). USUA_PERM_ADMIN no gobierna esta pantalla.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { mensajeError } from "$lib/utils/apiError.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, fetch }) {
  const { user } = await parent();

  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  const userId = user?.sub ?? "";

  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${token}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;

  let agrupaciones = [];
  let agrupacionesError = null;
  try {
    const res = await fetch(`${API_V1}/tvd?size=200`, { headers });
    if (res.ok) {
      agrupaciones = await res.json().catch(() => []);
    } else {
      const body = await res.json().catch(() => ({}));
      agrupacionesError = mensajeError(body, `Error ${res.status}`);
    }
  } catch {
    agrupacionesError = "No se pudo conectar al servidor de TVD.";
  }

  return { agrupaciones, agrupacionesError };
}
