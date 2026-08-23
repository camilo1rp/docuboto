// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /login — Inicio del flujo OAuth2 Authorization Code + PKCE.
 *
 * Estrategia:
 *  1. Servidor genera un state anti-CSRF y un code_verifier PKCE.
 *  2. Ambos se guardan en cookies httpOnly de vida corta (300 s).
 *  3. El servidor redirige al navegador al Authorization Endpoint de Keycloak
 *     con el code_challenge (SHA-256 del verifier).
 *  4. Keycloak autentica al usuario y redirige a /login/callback con el code.
 *
 * El code_verifier NUNCA llega al cliente JavaScript.
 */

import { redirect } from "@sveltejs/kit";
import {
  COOKIE_ACCESS_TOKEN,
  storePkceVerifierCookie,
  storePkceStateCookie,
} from "$lib/services/auth.server.js";
import { KEYCLOAK_AUTH_URL, KEYCLOAK_CLIENT_ID } from "$lib/utils/config.js";

/** Longitud del code_verifier en bytes → 43 chars base64url */
const VERIFIER_BYTES = 32;

/**
 * Mensajes en español para los códigos de error que puede reenviar
 * /login/callback. Se usa un mapa cerrado (nunca el string crudo de
 * Keycloak) porque `error` puede contener el `error_description` del
 * proveedor, y ese texto no es de confianza para reflejarlo tal cual.
 */
const LOGIN_ERROR_MESSAGES = {
  access_denied: "Se canceló el inicio de sesión.",
  state_missing: "La sesión de inicio de sesión expiró. Intenta de nuevo.",
  state_mismatch: "La sesión de inicio de sesión expiró. Intenta de nuevo.",
  verifier_missing: "La sesión de inicio de sesión expiró. Intenta de nuevo.",
  invalid_callback: "No se pudo completar el inicio de sesión. Intenta de nuevo.",
  authentication_failed:
    "No fue posible autenticarte con el proveedor de identidad. Intenta de nuevo o contacta al administrador de tu entidad.",
};

const LOGIN_ERROR_FALLBACK =
  "No fue posible iniciar sesión. Intenta de nuevo o contacta al administrador de tu entidad.";

/**
 * load — Si ya hay sesión activa, redirige al dashboard.
 * Si `/login/callback` reenvió un error (?error=...), se muestra en esta
 * misma página en vez de reiniciar el flujo PKCE — de lo contrario un error
 * persistente (consentimiento cancelado, cuenta deshabilitada) produce un
 * bucle infinito Keycloak↔/login.
 * En cualquier otro caso, genera el estado PKCE, guarda en cookies httpOnly
 * y redirige al Authorization Endpoint de Keycloak.
 *
 * @param {import('@sveltejs/kit').RequestEvent} event
 */
export async function load({ cookies, url }) {
  // Si /login/callback reenvió un error, mostrarlo aquí y NO reiniciar el
  // flujo — evita el bucle de redirects hacia Keycloak.
  const errorParam = url.searchParams.get("error");
  if (errorParam) {
    return { error: LOGIN_ERROR_MESSAGES[errorParam] ?? LOGIN_ERROR_FALLBACK };
  }

  // Si ya hay sesión válida, no mostrar el login
  const existingToken = cookies.get(COOKIE_ACCESS_TOKEN);
  if (existingToken) {
    redirect(302, "/dashboard");
  }

  // Parámetro `next` para redirigir tras login exitoso
  const next = url.searchParams.get("next") ?? "/dashboard";

  // Generar code_verifier y state server-side usando Node crypto
  const { randomBytes, createHash } = await import("crypto");

  const verifierBytes = randomBytes(VERIFIER_BYTES);
  const codeVerifier = base64url(verifierBytes);

  const stateBytes = randomBytes(16);
  const state = base64url(stateBytes) + "|" + encodeURIComponent(next);

  // Calcular code_challenge = BASE64URL(SHA256(verifier))
  const challengeBuffer = createHash("sha256").update(codeVerifier, "ascii").digest();
  const codeChallenge = base64url(challengeBuffer);

  // Guardar verifier y state en cookies httpOnly (vida corta)
  storePkceVerifierCookie(cookies, codeVerifier);
  storePkceStateCookie(cookies, state);

  // Construir redirect_uri: usar el origen de la request actual
  const redirectUri = `${url.origin}/login/callback`;

  // Construir URL de autorización de Keycloak
  const authUrl = new URL(KEYCLOAK_AUTH_URL);
  authUrl.searchParams.set("response_type", "code");
  authUrl.searchParams.set("client_id", KEYCLOAK_CLIENT_ID);
  authUrl.searchParams.set("redirect_uri", redirectUri);
  authUrl.searchParams.set("scope", "openid profile email");
  authUrl.searchParams.set("state", state);
  authUrl.searchParams.set("code_challenge", codeChallenge);
  authUrl.searchParams.set("code_challenge_method", "S256");
  // `ui_locales` (OIDC Core §3.1.2.1): la aplicación DECLARA en qué lengua
  // trabaja. Sin esto, Keycloak negocia con el `Accept-Language` del navegador
  // y un funcionario con el navegador en inglés veía «Sign in to your account»
  // seguido de una aplicación enteramente en español — la misma incoherencia,
  // movida de sitio. `defaultLocale: es` en el realm NO basta: es un respaldo
  // para cuando el navegador no expresa preferencia, y siempre la expresa.
  // El selector de idioma del tema sigue ahí para quien necesite otra.
  authUrl.searchParams.set("ui_locales", "es");

  redirect(302, authUrl.toString());
}

// ---------------------------------------------------------------------------
// Helper base64url (Node.js Buffer)
// ---------------------------------------------------------------------------

/**
 * Codifica un Buffer en base64url sin padding.
 * @param {Buffer} buffer
 * @returns {string}
 */
function base64url(buffer) {
  return buffer.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
}
