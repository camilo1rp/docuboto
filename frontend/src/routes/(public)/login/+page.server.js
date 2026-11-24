// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /login — Formulario de inicio de sesión (ADR-048).
 *
 * La identidad la emite la app: la acción envía usuario y contraseña a
 * `POST /api/v1/auth/login` desde el SSR, guarda los tokens en cookies httpOnly
 * y redirige a `next`. La contraseña nunca la ve el JavaScript del navegador
 * más allá del propio formulario, y el token nunca.
 *
 * La entidad sale de la configuración de despliegue (`PORTAL_TENANT_SLUG`,
 * una instalación por entidad, ADR-047). Si no está configurada, el formulario
 * la pide.
 */

import { fail, redirect } from "@sveltejs/kit";
import {
  COOKIE_ACCESS_TOKEN,
  LoginError,
  destinoSeguro,
  login,
  setSessionCookies,
  tokenVigente,
} from "$lib/services/auth.server.js";
import { PUBLIC_TENANT } from "$lib/utils/config.server.js";

/**
 * Mensajes en español para los códigos que devuelve el backend. Mapa cerrado:
 * nunca se refleja el texto crudo de una respuesta.
 */
const LOGIN_ERROR_MESSAGES = {
  invalid_credentials: "Usuario o contraseña incorrectos.",
  account_locked:
    "La cuenta está bloqueada temporalmente por intentos fallidos. Intenta más tarde o contacta al administrador de tu entidad.",
  tenant_inactivo: "La entidad no está habilitada.",
  tenant_no_provisionado: "La entidad todavía no está lista.",
  tenant_estado_no_disponible: "No se pudo comprobar la entidad. Intenta de nuevo en un momento.",
};

const LOGIN_ERROR_FALLBACK =
  "No fue posible iniciar sesión. Intenta de nuevo o contacta al administrador de tu entidad.";

/** @param {import('@sveltejs/kit').RequestEvent} event */
export async function load({ cookies, url }) {
  if (tokenVigente(cookies.get(COOKIE_ACCESS_TOKEN))) {
    redirect(302, destinoSeguro(url.searchParams.get("next")));
  }
  return { pedirEntidad: !PUBLIC_TENANT };
}

export const actions = {
  /** @param {import('@sveltejs/kit').RequestEvent} event */
  default: async ({ request, cookies, url }) => {
    const form = await request.formData();
    const username = String(form.get("username") ?? "").trim();
    const password = String(form.get("password") ?? "");
    const tenant = PUBLIC_TENANT || String(form.get("tenant") ?? "").trim();

    if (!username || !password || !tenant) {
      return fail(400, { username, error: "Escribe tu usuario y tu contraseña." });
    }

    let tokenSet;
    try {
      tokenSet = await login(username, password, tenant);
    } catch (e) {
      const codigo = e instanceof LoginError ? e.code : "";
      const status = e instanceof LoginError && e.status >= 400 && e.status < 500 ? e.status : 503;
      return fail(status, {
        username,
        error: LOGIN_ERROR_MESSAGES[codigo] ?? LOGIN_ERROR_FALLBACK,
      });
    }

    setSessionCookies(cookies, tokenSet);
    redirect(303, destinoSeguro(url.searchParams.get("next")));
  },
};
