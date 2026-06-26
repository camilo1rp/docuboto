// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * auth.server.js — Lógica de autenticación OAuth2 PKCE para el lado servidor
 * de SvelteKit (+page.server.js, +layout.server.js, hooks.server.js).
 *
 * Estrategia de almacenamiento de tokens:
 *  - access_token   → cookie httpOnly, Secure, SameSite=Lax, vida corta (~5 min Keycloak)
 *  - refresh_token  → cookie httpOnly, Secure, SameSite=Lax, vida más larga (~30 min)
 *  - id_token       → NO persiste en cookie; se parsea en memoria para extraer claims
 *
 * Los tokens NUNCA se exponen al JavaScript del cliente. El store `auth.js`
 * (cliente) solo recibe los claims del JWT extraídos por el servidor.
 *
 * El code_verifier se transmite al servidor en una cookie httpOnly de vida
 * muy corta (solo dura el ida y vuelta del flujo de autorización).
 *
 * Este módulo solo se importa en archivos *.server.js / hooks.server.js;
 * SvelteKit previene su inclusión en el bundle cliente.
 */

import { dev } from "$app/environment";
import { KEYCLOAK_TOKEN_URL, KEYCLOAK_LOGOUT_URL, KEYCLOAK_CLIENT_ID } from "$lib/utils/config.js";

// ---------------------------------------------------------------------------
// Constantes de cookies
// ---------------------------------------------------------------------------

export const COOKIE_ACCESS_TOKEN = "op_access_token";
export const COOKIE_REFRESH_TOKEN = "op_refresh_token";
export const COOKIE_PKCE_VERIFIER = "op_pkce_verifier";
export const COOKIE_PKCE_STATE = "op_pkce_state";

/**
 * Opciones base para todas las cookies de sesión.
 * sameSite tipado como literal para satisfacer el type del setter de cookies de SvelteKit.
 */
const COOKIE_BASE = /** @type {const} */ ({
  path: "/",
  httpOnly: true,
  secure: !dev, // true en producción (requiere HTTPS); false solo en dev local
  sameSite: /** @type {"lax"} */ ("lax"),
});

// ---------------------------------------------------------------------------
// Intercambio de código de autorización por tokens (server-side)
// ---------------------------------------------------------------------------

/**
 * Intercambia el authorization_code por access_token + refresh_token.
 * Llamado desde el callback (+page.server.js) con el code_verifier de la cookie.
 *
 * @param {string} code          — authorization_code de Keycloak
 * @param {string} codeVerifier  — verifier PKCE (de cookie httpOnly)
 * @param {string} redirectUri   — debe coincidir exactamente con el redirect_uri usado al iniciar
 * @returns {Promise<TokenSet>}
 */
export async function exchangeCode(code, codeVerifier, redirectUri) {
  const body = new URLSearchParams({
    grant_type: "authorization_code",
    client_id: KEYCLOAK_CLIENT_ID,
    code,
    code_verifier: codeVerifier,
    redirect_uri: redirectUri,
  });

  const res = await fetch(KEYCLOAK_TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(
      `Token exchange failed: ${err.error_description ?? err.error ?? `HTTP ${res.status}`}`,
    );
  }

  return res.json();
}

// ---------------------------------------------------------------------------
// Refresh del access_token
// ---------------------------------------------------------------------------

/**
 * Renueva el access_token usando el refresh_token.
 * @param {string} refreshToken
 * @returns {Promise<TokenSet|null>} — null si el refresh_token ha expirado
 */
export async function refreshAccessToken(refreshToken) {
  const body = new URLSearchParams({
    grant_type: "refresh_token",
    client_id: KEYCLOAK_CLIENT_ID,
    refresh_token: refreshToken,
  });

  const res = await fetch(KEYCLOAK_TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });

  if (!res.ok) return null;
  return res.json();
}

// ---------------------------------------------------------------------------
// Logout en Keycloak (revoca la sesión SSO)
// ---------------------------------------------------------------------------

/**
 * Notifica a Keycloak del logout para invalidar la sesión SSO.
 * @param {string} refreshToken
 */
export async function revokeSession(refreshToken) {
  if (!refreshToken) return;
  const body = new URLSearchParams({
    client_id: KEYCLOAK_CLIENT_ID,
    refresh_token: refreshToken,
  });
  await fetch(KEYCLOAK_LOGOUT_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  }).catch(() => {
    // No bloquear el logout local si Keycloak no responde
  });
}

// ---------------------------------------------------------------------------
// Parseo del JWT (sin verificar firma — el backend re-valida)
// ---------------------------------------------------------------------------

/**
 * Decodifica el payload de un JWT sin verificar la firma.
 * La verificación real la hace Keycloak/api-gateway; aquí solo extraemos claims
 * para mostrar en la UI (roles, tenant, nombre, etc.).
 *
 * @param {string} token — JWT en formato header.payload.signature
 * @returns {JwtClaims|null}
 */
export function decodeJwt(token) {
  if (!token) return null;
  try {
    const [, payload] = token.split(".");
    // Base64url → JSON
    const json = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
    return JSON.parse(json);
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Extracción de claims relevantes para la UI
// ---------------------------------------------------------------------------

/**
 * Extrae los claims que la UI necesita del JWT de Keycloak.
 *
 * Keycloak pone los roles en `realm_access.roles[]`.
 * Los permisos granulares vienen del api-gateway via `/auth/me` (no del JWT).
 * El tenant_slug es un atributo del usuario en Keycloak → claim personalizado.
 *
 * @param {JwtClaims} claims — payload del JWT
 * @returns {SessionUser}
 */
export function extractSessionUser(claims) {
  if (!claims) return null;

  const roles = claims.realm_access?.roles ?? [];
  const tenantSlug = claims.tenant_slug ?? claims["tenant_slug"] ?? null;

  return {
    sub: claims.sub,
    email: claims.email ?? null,
    name: claims.name ?? claims.preferred_username ?? claims.sub,
    preferred_username: claims.preferred_username ?? null,
    roles,
    tenant_slug: tenantSlug,
    // Permisos granulares: se cargan de /auth/me post-login (ver layout.server.js)
    permissions: [],
  };
}

// ---------------------------------------------------------------------------
// Helpers de cookies
// ---------------------------------------------------------------------------

/**
 * Escribe las cookies de sesión (access + refresh) en la respuesta.
 * @param {import('@sveltejs/kit').Cookies} cookies
 * @param {TokenSet} tokenSet
 */
export function setSessionCookies(cookies, tokenSet) {
  // access_token: vida corta (usa expires_in de Keycloak, máx 10 min)
  const accessMaxAge = Math.min(tokenSet.expires_in ?? 300, 600);
  cookies.set(COOKIE_ACCESS_TOKEN, tokenSet.access_token, {
    ...COOKIE_BASE,
    maxAge: accessMaxAge,
  });

  // refresh_token: vida más larga (usa refresh_expires_in de Keycloak)
  if (tokenSet.refresh_token) {
    const refreshMaxAge = tokenSet.refresh_expires_in ?? 1800;
    cookies.set(COOKIE_REFRESH_TOKEN, tokenSet.refresh_token, {
      ...COOKIE_BASE,
      maxAge: refreshMaxAge,
    });
  }
}

/**
 * Elimina todas las cookies de sesión (logout local).
 * @param {import('@sveltejs/kit').Cookies} cookies
 */
export function clearSessionCookies(cookies) {
  cookies.delete(COOKIE_ACCESS_TOKEN, { path: "/" });
  cookies.delete(COOKIE_REFRESH_TOKEN, { path: "/" });
}

/**
 * Guarda el code_verifier PKCE en una cookie httpOnly de vida corta.
 * @param {import('@sveltejs/kit').Cookies} cookies
 * @param {string} verifier
 */
export function storePkceVerifierCookie(cookies, verifier) {
  cookies.set(COOKIE_PKCE_VERIFIER, verifier, {
    ...COOKIE_BASE,
    maxAge: 300, // 5 minutos — solo durante el flujo de autorización
  });
}

/**
 * Guarda el state CSRF en una cookie httpOnly de vida corta.
 * @param {import('@sveltejs/kit').Cookies} cookies
 * @param {string} state
 */
export function storePkceStateCookie(cookies, state) {
  cookies.set(COOKIE_PKCE_STATE, state, {
    ...COOKIE_BASE,
    maxAge: 300,
  });
}

/**
 * Elimina las cookies PKCE temporales tras el intercambio.
 * @param {import('@sveltejs/kit').Cookies} cookies
 */
export function clearPkceCookies(cookies) {
  cookies.delete(COOKIE_PKCE_VERIFIER, { path: "/" });
  cookies.delete(COOKIE_PKCE_STATE, { path: "/" });
}

// ---------------------------------------------------------------------------
// JSDoc types (solo documentación, sin runtime)
// ---------------------------------------------------------------------------

/**
 * @typedef {object} TokenSet
 * @property {string} access_token
 * @property {string} [refresh_token]
 * @property {string} [id_token]
 * @property {number} [expires_in]
 * @property {number} [refresh_expires_in]
 * @property {string} [token_type]
 */

/**
 * @typedef {object} JwtClaims
 * @property {string} sub
 * @property {string} [email]
 * @property {string} [name]
 * @property {string} [preferred_username]
 * @property {{ roles: string[] }} [realm_access]
 * @property {string} [tenant_slug]
 * @property {number} [exp]
 * @property {number} [iat]
 */

/**
 * @typedef {object} SessionUser
 * @property {string} sub
 * @property {string|null} email
 * @property {string} name
 * @property {string|null} preferred_username
 * @property {string[]} roles
 * @property {string|null} tenant_slug
 * @property {string[]} permissions
 */
