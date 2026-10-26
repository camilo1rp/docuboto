// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
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
 * Base interna de Keycloak, expuesta para que `hooks.server.js` sepa a qué URL
 * aplicar el tiempo límite de respuesta (ver `$lib/server/fetchTimeout.js`). Un
 * Keycloak que acepta la conexión y no contesta cuelga el login y el refresh
 * igual que un gateway mudo cuelga el resto.
 */
export const KEYCLOAK_INTERNAL_BASE = INTERNAL_KC_BASE;

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

// ---------------------------------------------------------------------------
// Entidad de las pantallas PÚBLICAS (ventanilla y consulta ciudadana)
// ---------------------------------------------------------------------------
//
// Las rutas públicas del backend cuelgan de `/public/{tenant}/…`, así que la
// zona pública necesita saber a qué entidad pertenece. Se resuelve por
// **configuración de despliegue**, no por un parámetro en la URL: un ciudadano
// entra al sitio de SU entidad (`radicacion.entidad.gov.co`) y no hay por qué
// explicarle qué es un inquilino, ni dejar que lo cambie escribiendo otro en la
// barra de direcciones.
//
// **Sin valor por defecto**: si no está configurada, las pantallas públicas
// dicen que la entidad no ha habilitado la radicación en línea, en vez de
// apuntar a un inquilino inventado. Un default aquí mandaría las peticiones de
// los ciudadanos a la entidad equivocada.
// NO se llama `PUBLIC_…`: ese prefijo lo reserva SvelteKit para lo que puede
// viajar al navegador, y `$env/dynamic/private` **excluye** esas variables. Con
// el nombre anterior la variable existía en el contenedor y llegaba aquí como
// `undefined`, así que las pantallas públicas se declaraban «no habilitadas»
// con la configuración puesta — un fallo mudo. El slug no se necesita en el
// cliente: el proxy lo usa server-side, y así no se expone nada de más.
export const PUBLIC_TENANT = env.PORTAL_TENANT_SLUG ?? "";
