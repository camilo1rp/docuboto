// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/grupos/+page.server.js — Carga SSR del listado de grupos (E08, Fase 5
 * cierre de desfase API↔UI).
 *
 * Trae tres catálogos de solo lectura:
 *   - grupos (GET /auth/groups)
 *   - permisos (GET /auth/permissions) — catálogo completo, para el panel de
 *     permisos por grupo del drawer de detalle
 *   - niveles de seguridad (GET /auth/security-levels) — catálogo, para el
 *     panel de clearance del drawer de detalle
 *
 * GAP DE BACKEND (verificado en `auth-service/app/routers/rbac.py` y
 * `clearance.py`): NO existe ningún endpoint de LECTURA de membresías de un
 * grupo, de sus permisos asignados ni de su clearance actual. Solo existen
 * altas/bajas ciegas (POST/DELETE members, PUT permissions, PUT clearance).
 * La página lo advierte explícitamente en el drawer de detalle — no es una
 * omisión de UI, es una limitación real del backend (ver rbac.py: no hay
 * `GET /groups/{id}/members` ni `GET /groups/{id}/permissions` ni
 * `GET /groups/{id}/clearance`).
 *
 * El access_token NO se expone — las escrituras van a /admin/grupos/api/*
 * (D-05), que leen locals.accessToken.
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
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  let groups = [];
  let loadError = null;

  try {
    const res = await fetch(`${API_V1}/auth/groups`, { headers });
    if (res.ok) {
      groups = await res.json().catch(() => []);
    } else {
      const body = await res.json().catch(() => ({}));
      loadError = mensajeError(body, `Error ${res.status}`);
    }
  } catch {
    loadError = "No se pudo conectar al servidor de grupos.";
  }

  // Catálogos AUXILIARES para el drawer de detalle: si fallan, no bloquean
  // la lista de grupos (solo el panel correspondiente del drawer queda vacío,
  // con su propio aviso).
  let permissions = [];
  try {
    const res = await fetch(`${API_V1}/auth/permissions`, { headers });
    if (res.ok) permissions = await res.json().catch(() => []);
  } catch {
    permissions = [];
  }

  let securityLevels = [];
  try {
    const res = await fetch(`${API_V1}/auth/security-levels`, { headers });
    if (res.ok) securityLevels = await res.json().catch(() => []);
  } catch {
    securityLevels = [];
  }

  return {
    groups,
    permissions,
    securityLevels,
    loadError,
  };
}
