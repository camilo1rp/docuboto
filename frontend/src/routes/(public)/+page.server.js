// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * / — Landing pública de Docuboto.
 *
 * `hooks.server.js` puebla `event.locals.user` cuando la cookie de sesión trae
 * un access_token válido (o refrescable). Si ya hay sesión activa, un usuario
 * autenticado no debe aterrizar en una página de mercadeo: se redirige de una
 * vez a /dashboard. Sin sesión, se sirve la landing normalmente.
 *
 * @param {import('@sveltejs/kit').RequestEvent} event
 */
import { redirect } from "@sveltejs/kit";

export async function load({ locals }) {
  if (locals.user) {
    redirect(302, "/dashboard");
  }

  return {};
}
