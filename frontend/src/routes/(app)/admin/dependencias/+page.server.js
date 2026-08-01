// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/dependencias/+page.server.js — Carga SSR del árbol de dependencias
 * (E14, Fase 5 cierre de desfase API↔UI).
 *
 * Trae dos vistas del mismo catálogo:
 *   - tree (GET /dependencias/tree) — para pintar el árbol (raíces con
 *     `children` anidados).
 *   - flat (GET /dependencias, sin filtro de activo) — para el selector
 *     "dependencia padre" del formulario de crear/editar (necesita el
 *     universo completo, incluidas las inactivas: reparentar bajo una
 *     dependencia inactiva sigue siendo una operación válida en el backend).
 *
 * El access_token NO se expone — las escrituras van a /admin/dependencias/api/*
 * (D-05), que leen locals.accessToken.
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
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  let tree = [];
  let loadError = null;

  try {
    const res = await fetch(`${API_V1}/dependencias/tree`, { headers });
    if (res.ok) {
      tree = await res.json().catch(() => []);
    } else {
      const body = await res.json().catch(() => ({}));
      loadError = body.detail ?? `Error ${res.status}`;
    }
  } catch {
    loadError = "No se pudo conectar al servidor de dependencias.";
  }

  // Catálogo AUXILIAR para el selector de "dependencia padre": si falla, no
  // bloquea el árbol (solo el selector del formulario queda sin opciones,
  // limitando la creación a dependencias raíz).
  let flat = [];
  try {
    const res = await fetch(`${API_V1}/dependencias`, { headers });
    if (res.ok) flat = await res.json().catch(() => []);
  } catch {
    flat = [];
  }

  return {
    tree,
    flat,
    loadError,
  };
}
