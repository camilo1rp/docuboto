// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/trd/+page.server.js — Carga SSR de series TRD/CCD y tipos documentales
 * (E04, Fase 5 cierre de desfase API↔UI).
 *
 * Dos catálogos independientes de archive-service:
 *   - GET /trd (paginado, X-Total-Count) — series jerárquicas (parent_id).
 *     Se pide `size=200` para traer el árbol completo de una vez: la vista
 *     construye el árbol en cliente y una paginación parcial rompería la
 *     jerarquía (una subserie visible sin su padre en la página actual).
 *   - GET /tipos-documentales (sin paginar) — cuelgan de una serie o van sueltos.
 *
 * RBAC real (espejo, D-11): el backend gatea creación/edición con
 * USUA_PERM_TRD (no USUA_PERM_ADMIN) — ver services/archive-service/app/
 * routers/trd.py y tipos_documentales.py. USUA_PERM_ADMIN solo controla la
 * VISIBILIDAD de la sección /admin en el dashboard (admin/+page.svelte);
 * dentro de esta pantalla el botón "Nueva serie"/"Nuevo tipo" se gatea con
 * el permiso real que el backend exige, para no ofrecer un botón que el
 * backend rechazaría a un USUA_PERM_ADMIN sin USUA_PERM_TRD, ni ocultarlo a
 * un administrador de TRD sin USUA_PERM_ADMIN general.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, fetch }) {
  const { user } = await parent();

  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  const userId = user?.sub ?? "";

  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${token}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;

  let series = [];
  let seriesError = null;
  try {
    const res = await fetch(`${API_V1}/trd?size=200`, { headers });
    if (res.ok) {
      series = await res.json().catch(() => []);
    } else {
      const body = await res.json().catch(() => ({}));
      seriesError = body.detail ?? `Error ${res.status}`;
    }
  } catch {
    seriesError = "No se pudo conectar al servidor de TRD.";
  }

  let tipos = [];
  let tiposError = null;
  try {
    const res = await fetch(`${API_V1}/tipos-documentales`, { headers });
    if (res.ok) {
      tipos = await res.json().catch(() => []);
    } else {
      const body = await res.json().catch(() => ({}));
      tiposError = body.detail ?? `Error ${res.status}`;
    }
  } catch {
    tiposError = "No se pudo conectar al servidor de tipos documentales.";
  }

  // Cola de revisión (ADR-025 D4/D6). Se carga aquí, no bajo demanda, porque
  // su razón de ser es que ALGUIEN la vea: son expedientes ya cerrados que el
  // backfill marcó y que sin pantalla quedaban marcados para siempre.
  let revision = [];
  let revisionError = null;
  try {
    const res = await fetch(`${API_V1}/trd/revision-pendiente?size=200`, { headers });
    if (res.ok) {
      revision = await res.json().catch(() => []);
    } else {
      const body = await res.json().catch(() => ({}));
      revisionError = body.detail ?? `Error ${res.status}`;
    }
  } catch {
    revisionError = "No se pudo conectar al servidor para consultar la cola de revisión.";
  }

  return { series, seriesError, tipos, tiposError, revision, revisionError };
}
