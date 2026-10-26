// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
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
 * (sección→subsección→serie del fondo, `parent_id`, ADR-026 D2). Se traen TODAS
 * las páginas, mismo motivo que admin/trd: la vista construye el árbol en
 * cliente y una carga parcial rompe la jerarquía. Antes se pedía `size=200`
 * «para traer el árbol completo de una vez» — pero 200 es el TOPE del backend,
 * no una holgura, así que con más agrupaciones el árbol perdía ramas en
 * silencio. Si aun así no cabe, `agrupacionesIncompletas` lo dice en pantalla.
 *
 * RBAC real: el backend gatea creación/edición/circuito con USUA_PERM_TRD
 * -- ADR-026 reutiliza a propósito el permiso de TRD (misma potestad
 * archivística, mismo Comité; ver services/archive-service/app/routers/
 * tvd.py). USUA_PERM_ADMIN no gobierna esta pantalla.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { mensajeError } from "$lib/utils/apiError.js";
import { traerListaCompleta } from "$lib/server/listaCompleta.server.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, fetch }) {
  const { user } = await parent();

  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  const userId = user?.sub ?? "";

  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${token}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;

  const cargadas = await traerListaCompleta(fetch, `${API_V1}/tvd`, headers, mensajeError);

  return {
    agrupaciones: cargadas.items,
    agrupacionesError:
      cargadas.error === "No se pudo conectar al servidor."
        ? "No se pudo conectar al servidor de TVD."
        : cargadas.error,
    agrupacionesIncompletas: cargadas.incompleta,
    agrupacionesTotal: cargadas.total,
  };
}
