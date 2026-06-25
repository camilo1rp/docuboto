// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * (app)/+layout.server.js — Guarda de sesión SSR para todas las rutas privadas.
 *
 * Ejecutado en cada request a cualquier ruta bajo (app)/:
 *  1. Verifica que exista un access_token en la cookie httpOnly.
 *  2. Si expiró, intenta refresh con el refresh_token.
 *  3. Si no hay tokens válidos → redirect a /login?next=<ruta-actual>.
 *  4. Si hay sesión → decodifica el JWT, extrae claims y carga /auth/me
 *     para obtener permisos granulares.
 *  5. Devuelve `user` (claims + permisos) al layout cliente y a todas
 *     las páginas hijas vía `data.user`.
 *
 * Los tokens quedan en cookies httpOnly; nunca en el bundle cliente.
 */

import { redirect } from "@sveltejs/kit";
import {
  COOKIE_ACCESS_TOKEN,
  COOKIE_REFRESH_TOKEN,
  refreshAccessToken,
  setSessionCookies,
  clearSessionCookies,
  decodeJwt,
  extractSessionUser,
} from "$lib/services/auth.server.js";
import { ENDPOINTS } from "$lib/utils/config.js";

/**
 * @param {import('@sveltejs/kit').RequestEvent} event
 * @returns {Promise<{ user: import('$lib/services/auth.server.js').SessionUser }>}
 */
export async function load({ cookies, url, fetch }) {
  let accessToken = cookies.get(COOKIE_ACCESS_TOKEN);
  const refreshToken = cookies.get(COOKIE_REFRESH_TOKEN);

  // --- Sin access_token: intentar refresh ---
  if (!accessToken && refreshToken) {
    const tokenSet = await refreshAccessToken(refreshToken);
    if (tokenSet) {
      setSessionCookies(cookies, tokenSet);
      accessToken = tokenSet.access_token;
    }
  }

  // --- Sin tokens válidos: redirect al login ---
  if (!accessToken) {
    clearSessionCookies(cookies);
    const next = encodeURIComponent(url.pathname + url.search);
    redirect(302, `/login?next=${next}`);
  }

  // --- Extraer claims básicos del JWT ---
  const claims = decodeJwt(accessToken);
  if (!claims) {
    clearSessionCookies(cookies);
    redirect(302, "/login?error=invalid_token");
  }

  const user = extractSessionUser(claims);

  // --- Cargar permisos granulares desde /auth/me ---
  // Se hace con el access_token de la cookie; SvelteKit pasa el fetch del servidor.
  try {
    const meData = await fetch(ENDPOINTS.auth.me, {
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "X-Tenant-Slug": user.tenant_slug ?? "",
      },
    });

    if (meData.ok) {
      const me = await meData.json();
      user.permissions = me.permissions ?? [];
      // Si el backend enriquece el nombre/email, actualizar
      if (me.name) user.name = me.name;
      if (me.email) user.email = me.email;
    }
    // Si /auth/me falla (503, etc.), continuamos con permisos vacíos
    // y el RBAC del backend protegerá igualmente las acciones.
  } catch {
    // Error de red hacia el gateway — se degrada sin permisos granulares
    user.permissions = [];
  }

  return { user };
}
