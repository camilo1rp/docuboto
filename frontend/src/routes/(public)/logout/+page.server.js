// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /logout — Cierre de sesión.
 *
 * Pasos:
 *  1. Lee el refresh_token de la cookie httpOnly.
 *  2. Notifica a Keycloak para invalidar la sesión SSO (end_session_endpoint).
 *  3. Elimina las cookies de sesión locales.
 *  4. Redirige a /login.
 *
 * El usuario puede llegar aquí por GET (link en la topbar) o POST
 * (form action) — ambos soportados.
 */

import { redirect } from "@sveltejs/kit";
import {
  COOKIE_REFRESH_TOKEN,
  clearSessionCookies,
  revokeSession,
} from "$lib/services/auth.server.js";
import { KEYCLOAK_LOGOUT_URL, KEYCLOAK_CLIENT_ID } from "$lib/utils/config.js";

/** @param {import('@sveltejs/kit').RequestEvent} event */
export async function load({ cookies, url }) {
  await performLogout(cookies, url);
}

/** Form action para POST desde un formulario */
export const actions = {
  default: async ({ cookies, url }) => {
    await performLogout(cookies, url);
  },
};

/**
 * Lógica de logout compartida entre GET y POST.
 * @param {import('@sveltejs/kit').Cookies} cookies
 * @param {URL} url
 */
async function performLogout(cookies, url) {
  const refreshToken = cookies.get(COOKIE_REFRESH_TOKEN);

  // Invalidar sesión en Keycloak (best-effort, no bloquea el logout local)
  if (refreshToken) {
    await revokeSession(refreshToken);
  }

  // Eliminar cookies de sesión locales
  clearSessionCookies(cookies);

  // Construir URL de logout de Keycloak (post_logout_redirect_uri)
  // Opcional: redirigir al usuario por Keycloak para limpiar SSO en el browser
  const postLogoutUri = `${url.origin}/login`;
  const keycloakLogoutUrl = new URL(KEYCLOAK_LOGOUT_URL);
  keycloakLogoutUrl.searchParams.set("client_id", KEYCLOAK_CLIENT_ID);
  keycloakLogoutUrl.searchParams.set("post_logout_redirect_uri", postLogoutUri);

  // Redirigir al logout de Keycloak para limpiar también la sesión SSO del browser
  redirect(302, keycloakLogoutUrl.toString());
}
