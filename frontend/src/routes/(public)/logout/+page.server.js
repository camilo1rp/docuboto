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
 *  2. Lo revoca en la app (`POST /api/v1/auth/logout`, ADR-048).
 *  3. Elimina las cookies de sesión locales.
 *  4. Redirige a /login.
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

/** Form action para POST desde un formulario — el ÚNICO camino que cierra sesión */
export const actions = {
  default: async ({ cookies }) => {
    await performLogout(cookies);
  },
};

/**
 * Revoca la sesión, borra las cookies y manda a /login.
 * @param {import('@sveltejs/kit').Cookies} cookies
 */
async function performLogout(cookies) {
  const refreshToken = cookies.get(COOKIE_REFRESH_TOKEN);

  // Revocar en la app (best-effort, no bloquea el logout local)
  if (refreshToken) {
    await revokeSession(refreshToken);
  }

  clearSessionCookies(cookies);
  redirect(303, "/login");
}
