// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /login/callback — Callback del flujo OAuth2 Authorization Code + PKCE.
 *
 * Keycloak redirige aquí con `?code=...&state=...` tras la autenticación.
 *
 * Pasos:
 *  1. Validar el state anti-CSRF contra la cookie httpOnly.
 *  2. Recuperar el code_verifier de la cookie httpOnly.
 *  3. Intercambiar el code por tokens (POST al token endpoint de Keycloak).
 *  4. Guardar access_token y refresh_token en cookies httpOnly.
 *  5. Eliminar las cookies PKCE temporales.
 *  6. Redirigir al usuario a `next` (extraído del state) o al dashboard.
 *
 * Si hay algún error, redirigir a /login con query param `error`.
 */

import { redirect } from "@sveltejs/kit";
import {
  COOKIE_PKCE_VERIFIER,
  COOKIE_PKCE_STATE,
  exchangeCode,
  setSessionCookies,
  clearPkceCookies,
} from "$lib/services/auth.server.js";

/**
 * @param {import('@sveltejs/kit').RequestEvent} event
 */
export async function load({ url, cookies }) {
  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state");
  const errorParam = url.searchParams.get("error");

  // El usuario canceló o Keycloak devolvió error
  if (errorParam) {
    const desc = url.searchParams.get("error_description") ?? errorParam;
    redirect(302, `/login?error=${encodeURIComponent(desc)}`);
  }

  if (!code || !state) {
    redirect(302, "/login?error=invalid_callback");
  }

  // --- Validar state anti-CSRF ---
  const storedState = cookies.get(COOKIE_PKCE_STATE);
  if (!storedState) {
    redirect(302, "/login?error=state_missing");
  }

  // state = <random>|<encodedNext>
  const [storedRandom] = storedState.split("|");
  const [receivedRandom, encodedNext] = state.split("|");

  if (storedRandom !== receivedRandom) {
    redirect(302, "/login?error=state_mismatch");
  }

  // Destino post-login
  const next = encodedNext ? decodeURIComponent(encodedNext) : "/dashboard";
  // Seguridad: rechazar open redirect a hosts externos.
  // Un path que empiece por "//" o "/\" es una URL protocolo-relativa o
  // una barra de Windows — ambas redirigen fuera del origen en algunos browsers.
  // Usamos URL() para verificar que el destino pertenece al mismo origen.
  let safeNext = "/dashboard";
  try {
    const candidate = new URL(next, url.origin);
    if (candidate.origin === url.origin) {
      safeNext = candidate.pathname + candidate.search + candidate.hash;
    }
  } catch {
    // next no es un path válido — usar el dashboard por defecto
  }

  // --- Recuperar code_verifier ---
  const codeVerifier = cookies.get(COOKIE_PKCE_VERIFIER);
  if (!codeVerifier) {
    redirect(302, "/login?error=verifier_missing");
  }

  // --- Intercambiar code por tokens ---
  const redirectUri = `${url.origin}/login/callback`;

  let tokenSet;
  try {
    tokenSet = await exchangeCode(code, codeVerifier, redirectUri);
  } catch (err) {
    console.error("[callback] Token exchange error:", err?.message);
    redirect(302, `/login?error=${encodeURIComponent("authentication_failed")}`);
  }

  // --- Guardar tokens en cookies httpOnly ---
  setSessionCookies(cookies, tokenSet);

  // --- Limpiar cookies PKCE temporales ---
  clearPkceCookies(cookies);

  // --- Redirigir al destino ---
  redirect(302, safeNext);
}
