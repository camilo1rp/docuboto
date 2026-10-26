// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /logout — Cierre de sesión. **Solo por POST.**
 *
 * Pasos:
 *  1. Lee el refresh_token de la cookie httpOnly.
 *  2. Notifica a Keycloak para invalidar la sesión SSO (end_session_endpoint).
 *  3. Elimina las cookies de sesión locales.
 *  4. Redirige al end_session_endpoint de Keycloak, que devuelve a /login.
 *
 * **Por qué ya no hay `load`.** Antes el cierre de sesión se ejecutaba también
 * en el `load` de la ruta, es decir, en un GET. El enlace del topbar era un
 * `<a href="/logout">` y `app.html` declara `data-sveltekit-preload-data="hover"`:
 * SvelteKit precarga el `load` de un enlace cuando el puntero pasa por encima,
 * así que **rozar el icono cerraba la sesión** sin llegar a hacer clic. Con el
 * efecto en un GET el problema es más ancho que ese icono: lo dispara cualquier
 * cosa que "visite" la URL —un prefetch del navegador, un escáner de enlaces,
 * un `<img src>` remoto apuntando aquí—, que es la forma clásica del CSRF de
 * cierre de sesión.
 *
 * Ahora el efecto vive solo en la `action` POST, que pasa por la comprobación
 * de origen de SvelteKit. Un GET a esta ruta renderiza `+page.svelte`, que es
 * un botón de confirmación: no cierra nada por sí mismo.
 */

import { redirect } from "@sveltejs/kit";
import {
  COOKIE_REFRESH_TOKEN,
  clearSessionCookies,
  revokeSession,
} from "$lib/services/auth.server.js";
import { KEYCLOAK_LOGOUT_URL, KEYCLOAK_CLIENT_ID } from "$lib/utils/config.js";

/** Form action para POST desde un formulario — el ÚNICO camino que cierra sesión */
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
