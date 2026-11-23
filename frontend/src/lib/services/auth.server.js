// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * auth.server.js — Sesión del lado servidor de SvelteKit (+page.server.js,
 * +layout.server.js, hooks.server.js).
 *
 * La identidad la emite la app (ADR-048): `POST /api/v1/auth/login` con usuario
 * y contraseña, `/refresh` que rota el refresh_token y `/logout` que lo revoca.
 * El navegador nunca habla con el emisor: todo pasa por el SSR.
 *
 * Estrategia de almacenamiento de tokens:
 *  - access_token   → cookie httpOnly, Secure, SameSite=Lax, vida corta (15 min)
 *  - refresh_token  → cookie httpOnly, Secure, SameSite=Lax, 12 h como máximo;
 *                     rota en cada uso (el anterior deja de valer)
 *
 * Los tokens NUNCA se exponen al JavaScript del cliente. El store `auth.js`
 * (cliente) solo recibe los claims del JWT extraídos por el servidor.
 *
 * Este módulo solo se importa en archivos *.server.js / hooks.server.js;
 * SvelteKit previene su inclusión en el bundle cliente.
 */

import { dev } from "$app/environment";
import { API_V1 } from "$lib/utils/config.server.js";
import { codigoError } from "$lib/utils/apiError.js";

// ---------------------------------------------------------------------------
// Constantes de cookies
// ---------------------------------------------------------------------------

export const COOKIE_ACCESS_TOKEN = "op_access_token";
export const COOKIE_REFRESH_TOKEN = "op_refresh_token";

/** Vida máxima de la sesión (la del refresh_token en el backend, ADR-048). */
const REFRESH_MAX_AGE_S = 12 * 60 * 60;

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
// Inicio de sesión
// ---------------------------------------------------------------------------

/** Error de inicio de sesión con el código del backend (`invalid_credentials`, …). */
export class LoginError extends Error {
  /**
   * @param {string} code
   * @param {number} status
   */
  constructor(code, status) {
    super(code);
    this.code = code;
    this.status = status;
  }
}

/**
 * Pide los tokens con usuario y contraseña.
 * @param {string} username
 * @param {string} password
 * @param {string} tenantSlug
 * @returns {Promise<TokenSet>}
 */
export async function login(username, password, tenantSlug) {
  const res = await fetch(`${API_V1}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password, tenant_slug: tenantSlug }),
  });
  if (!res.ok) {
    const cuerpo = await res.json().catch(() => ({}));
    throw new LoginError(codigoError(cuerpo) ?? "login_failed", res.status);
  }
  return res.json();
}

/**
 * `next` solo si es una ruta interna: un `next=https://otro.sitio` convertiría
 * el login en un redirector abierto.
 * @param {string|null} next
 * @returns {string}
 */
export function destinoSeguro(next) {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) {
    return "/dashboard";
  }
  return next;
}

// ---------------------------------------------------------------------------
// Refresh del access_token
// ---------------------------------------------------------------------------

/**
 * Renueva el access_token usando el refresh_token (que rota).
 * @param {string} refreshToken
 * @returns {Promise<TokenSet|null>} — null si el refresh_token ya no vale
 */
export async function refreshAccessToken(refreshToken) {
  const res = await fetch(`${API_V1}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  if (!res.ok) return null;
  return res.json();
}

// ---------------------------------------------------------------------------
// Cierre de sesión (revoca el refresh_token)
// ---------------------------------------------------------------------------

/**
 * Revoca la sesión en el backend. Best-effort: el cierre local no espera.
 * @param {string} refreshToken
 */
export async function revokeSession(refreshToken) {
  if (!refreshToken) return;
  await fetch(`${API_V1}/auth/logout`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  }).catch(() => {
    // No bloquear el logout local si la app no responde
  });
}

// ---------------------------------------------------------------------------
// Parseo del JWT (sin verificar firma — el backend re-valida)
// ---------------------------------------------------------------------------

/**
 * Decodifica el payload de un JWT sin verificar la firma.
 * La verificación real la hace la app en cada petición; aquí solo extraemos claims
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

/**
 * Margen de seguridad para dar por agotado un access_token ANTES de que el
 * emisor lo rechace. Cubre el desfase entre el reloj del emisor —que fija
 * `exp` en el instante en que acuña el token— y el del navegador, que empieza a
 * contar el `maxAge` de la cookie cuando la recibe: la cookie sobrevive
 * siempre al token por, al menos, el viaje de ida y vuelta.
 */
export const MARGEN_EXPIRACION_S = 15;

/**
 * ¿Queda vida útil en este access_token?
 *
 * Toda la sesión del frontend descansaba en una premisa que nadie comprobaba:
 * que la cookie desaparece exactamente cuando el token caduca. No es cierto —la
 * cookie se fija con un `maxAge` relativo al reloj del navegador y `exp` es
 * absoluto, del reloj del emisor— y la consecuencia se ve desde la primera
 * pantalla: con la cookie presente y el token muerto, la guarda daba la sesión
 * por buena, el refresco no se intentaba (solo corre si la cookie FALTA) y
 * cada pantalla se dibujaba entera con «El token es inválido o expiró» dentro.
 * El funcionario tenía un refresh_token vivo en el tarro y nadie lo usaba.
 *
 * Un token que no se puede decodificar se declara agotado: no se puede afirmar
 * que sirve, y la respuesta segura es renovarlo o mandar a login.
 *
 * @param {string|undefined} token
 * @param {number} [margenS] - segundos de antelación con que se da por agotado
 * @returns {boolean}
 */
export function tokenVigente(token, margenS = MARGEN_EXPIRACION_S) {
  if (!token) return false;
  const claims = decodeJwt(token);
  // Sin `exp` no hay forma de saberlo. El emisor siempre lo pone; si un día
  // faltara, tratarlo como vigente devolvería el defecto que esto corrige.
  if (!claims || typeof claims.exp !== "number") return false;
  return claims.exp * 1000 > Date.now() + margenS * 1000;
}

// ---------------------------------------------------------------------------
// Extracción de claims relevantes para la UI
// ---------------------------------------------------------------------------

/**
 * Extrae los claims que la UI necesita del access token (ADR-048).
 *
 * `sub` ya es `auth_users.id`. Los permisos granulares vienen de `/auth/me`
 * (no del JWT); el token no lleva roles.
 *
 * @param {JwtClaims} claims — payload del JWT
 * @returns {SessionUser}
 */
export function extractSessionUser(claims) {
  if (!claims) return null;

  const tenantSlug = claims.tenant_slug ?? claims["tenant_slug"] ?? null;

  return {
    sub: claims.sub,
    email: claims.email ?? null,
    name: claims.name ?? claims.preferred_username ?? claims.sub,
    preferred_username: claims.preferred_username ?? null,
    roles: [],
    tenant_slug: tenantSlug,
    // Permisos granulares (+ niveles CRUD): se cargan de /auth/me post-login
    // (ver layout.server.js). Fail-closed: `{}` hasta que /auth/me responda.
    permissions: [],
    permission_levels: {},
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
  // access_token: vida corta (expires_in del emisor, máx 15 min)
  const accessMaxAge = Math.min(tokenSet.expires_in ?? 300, 900);
  cookies.set(COOKIE_ACCESS_TOKEN, tokenSet.access_token, {
    ...COOKIE_BASE,
    maxAge: accessMaxAge,
  });

  // refresh_token: la sesión entera. El backend lo vence antes por inactividad
  // (30 min) o por tope absoluto (12 h); la cookie no necesita saberlo.
  if (tokenSet.refresh_token) {
    cookies.set(COOKIE_REFRESH_TOKEN, tokenSet.refresh_token, {
      ...COOKIE_BASE,
      maxAge: REFRESH_MAX_AGE_S,
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

// ---------------------------------------------------------------------------
// JSDoc types (solo documentación, sin runtime)
// ---------------------------------------------------------------------------

/**
 * @typedef {object} TokenSet
 * @property {string} access_token
 * @property {string} [refresh_token]
 * @property {number} [expires_in]
 * @property {string} [token_type]
 */

/**
 * @typedef {object} JwtClaims
 * @property {string} sub
 * @property {string} [email]
 * @property {string} [name]
 * @property {string} [preferred_username]
 * @property {string} [tenant_slug]
 * @property {number} [exp]
 * @property {number} [iat]
 */

/**
 * @typedef {object} SessionUser
 * @property {string} sub
 *   `sub` del access token: ya es `auth_users.id` (ADR-048). `user_id`, que
 *   resuelve `/auth/me`, es el mismo valor.
 * @property {string} [user_id]
 *   Identidad INTERNA del tenant (`auth_users.id`), resuelta por `/auth/me`.
 *   Es la que el backend escribe en las columnas de autoría (`creada_por`,
 *   `solicitado_por`, `firmante_id`…), así que es la única con la que tiene
 *   sentido preguntar «¿esto lo hice yo?».
 * @property {string|null} email
 * @property {string} name
 * @property {string|null} preferred_username
 * @property {string[]} roles
 * @property {string|null} tenant_slug
 * @property {string[]} permissions
 * @property {Record<string, number>} [permission_levels]
 *   Nivel CRUD por permiso (`GET /auth/me`, aditivo a `permissions[]`);
 *   fail-closed `{}` si el backend no lo trae.
 * @property {{user_id: string, active_depe_id: number|null, available_depts: Array<object>}|null} [context]
 *   Contexto de dependencia activa (E08/ADR-013) — poblado por
 *   `(app)/+layout.server.js`, best-effort (puede quedar `null`).
 * @property {boolean} [asistente_habilitado]
 *   La entidad activó el asistente (ADR-037, `GET /auth/me`); `false` si no se pudo comprobar.
 * @property {"local"|"ppi"} [intelligence_provider]
 *   Proveedor de inteligencia de la entidad (`GET /auth/me`); `"local"` si no se pudo comprobar.
 * @property {boolean} [permisosDegradados]
 *   `true` cuando `GET /auth/me` no respondió: la sesión sigue viva pero sin
 *   permisos granulares. Sin este campo, el menú vacío que resulta es
 *   indistinguible de «me revocaron los permisos» — `AppLayout.svelte` lo usa
 *   para decirlo en pantalla.
 * @property {Array<{codigo: number, nombre: string, sigla: string}>} [dependencias]
 *   Catálogo mínimo de dependencias activas del tenant, para resolver la
 *   etiqueta legible de `context.active_depe_id` en `DependencySwitcher.svelte`.
 */
