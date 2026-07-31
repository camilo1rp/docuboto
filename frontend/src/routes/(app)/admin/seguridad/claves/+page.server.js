// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/seguridad/claves/+page.server.js — Carga SSR del universo de
 * usuarios del tenant (Fase 3, cierre de desfase API↔UI) para que un
 * administrador pueda elegir a QUIÉN revocar la credencial de firma
 * personal PKI.
 *
 * `GET /auth/users` (USUA_PERM_ADMIN, `services/auth-service/app/routers/rbac.py`)
 * — mismo gate que ya exige esta subruta a nivel de navegación. NO existe
 * un endpoint que liste el estado de firma por usuario (solo
 * `GET /auth/me/signing-key`, propio) ni uno de búsqueda por username —
 * se trae una página amplia (100) y se filtra en cliente; ver nota en
 * `+page.svelte` sobre por qué la pantalla es "revocación a ciegas" (el
 * admin no ve de antemano si el usuario objetivo tiene una credencial
 * activa — el backend responde 404 `no_signing_key` si no la tiene).
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, fetch }) {
  const { user } = await parent();

  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  const userId = user?.sub ?? "";

  let users = [];
  let loadError = null;

  try {
    /** @type {Record<string, string>} */
    const headers = {
      Authorization: `Bearer ${token}`,
      "X-Tenant-Slug": tenant,
    };
    if (userId) headers["X-User-Id"] = userId;

    const res = await fetch(`${API_V1}/auth/users?page=1&size=100`, { headers });
    if (res.ok) {
      const raw = await res.json().catch(() => []);
      users = (Array.isArray(raw) ? raw : []).filter((u) => u.id !== userId);
    } else {
      const body = await res.json().catch(() => ({}));
      loadError = body.detail ?? `Error ${res.status}`;
    }
  } catch {
    loadError = "No se pudo cargar la lista de usuarios.";
  }

  return { users, loadError };
}
