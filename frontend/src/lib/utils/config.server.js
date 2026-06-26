// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * config.server.js — Configuración SERVER-ONLY de OrpycaMCP.
 *
 * Contiene las URLs internas que usa el servidor SSR (adapter-node) dentro de
 * la red Docker para alcanzar el api-gateway y Keycloak.
 *
 * REGLA: Este módulo NO debe importarse desde archivos .svelte ni desde ningún
 * módulo que se incluya en el bundle de cliente. SvelteKit lo refuerza: los
 * archivos *.server.js solo se cargan en contexto de servidor.
 *
 * - INTERNAL_API_URL  (env privada) → URL del api-gateway en la red Docker,
 *     ej. http://api-gateway:8080.  Usada por +page.server.js y +server.js
 *     para hacer fetch al gateway.  En el navegador, el gateway es alcanzable
 *     en PUBLIC_API_URL (config.js) — vía el host, no la red Docker.
 *
 * - INTERNAL_KEYCLOAK_URL (env privada) → URL de Keycloak en la red Docker,
 *     ej. http://keycloak:8080. Usada para token exchange, refresh y revocación
 *     back-channel. El issuer (iss) de los tokens SIEMPRE es la URL pública
 *     (KC_HOSTNAME_URL=http://localhost:19180), lo que es correcto e intencional.
 *
 * Las URLs PÚBLICAS (para el navegador del usuario) están en config.js.
 */

import { env } from "$env/dynamic/private";

const REALM = "orpycamcp";

// ---------------------------------------------------------------------------
// api-gateway — URL interna (accesible desde el contenedor SSR)
// ---------------------------------------------------------------------------

const INTERNAL_API_BASE = env.INTERNAL_API_URL ?? "http://api-gateway:8080";

/** Prefijo versionado del api-gateway para llamadas server-side (SSR, +server.js). */
export const API_V1 = `${INTERNAL_API_BASE}/api/v1`;

// ---------------------------------------------------------------------------
// Keycloak — URLs internas para llamadas back-channel (token exchange, refresh)
// ---------------------------------------------------------------------------

const INTERNAL_KC_BASE = env.INTERNAL_KEYCLOAK_URL ?? "http://keycloak:8080";
const KC_OIDC = `${INTERNAL_KC_BASE}/realms/${REALM}/protocol/openid-connect`;

/**
 * URL del Token Endpoint de Keycloak para intercambio de código y refresh
 * (server-side only — back-channel, nunca expuesta al navegador).
 */
export const KEYCLOAK_TOKEN_URL = `${KC_OIDC}/token`;

/**
 * URL del Logout Endpoint de Keycloak para revocación back-channel del
 * refresh_token (server-side only). No confundir con la URL pública de logout
 * de usuario (que está en config.js y usa el PUBLIC_KEYCLOAK_URL para la
 * redirección del navegador al end_session_endpoint).
 */
export const KEYCLOAK_LOGOUT_URL = `${KC_OIDC}/logout`;

/**
 * URL del UserInfo Endpoint de Keycloak (server-side only).
 * Permite obtener claims adicionales del usuario desde el servidor SSR.
 */
export const KEYCLOAK_USERINFO_URL = `${KC_OIDC}/userinfo`;
