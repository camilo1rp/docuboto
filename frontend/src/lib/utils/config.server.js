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
 * Contiene la URL interna que usa el servidor SSR (adapter-node) dentro de la
 * red Docker para alcanzar la app (API y emisor de identidad, ADR-048).
 *
 * REGLA: Este módulo NO debe importarse desde archivos .svelte ni desde ningún
 * módulo que se incluya en el bundle de cliente. SvelteKit lo refuerza: los
 * archivos *.server.js solo se cargan en contexto de servidor.
 *
 * - INTERNAL_API_URL  (env privada) → URL del api-gateway en la red Docker,
 *     ej. http://app:8000.  Usada por +page.server.js y +server.js
 *     para hacer fetch al gateway.  En el navegador, el gateway es alcanzable
 *     en PUBLIC_API_URL (config.js) — vía el host, no la red Docker.
 *
 * Las URLs PÚBLICAS (para el navegador del usuario) están en config.js.
 */

import { env } from "$env/dynamic/private";

// ---------------------------------------------------------------------------
// api-gateway — URL interna (accesible desde el contenedor SSR)
// ---------------------------------------------------------------------------

const INTERNAL_API_BASE = env.INTERNAL_API_URL ?? "http://app:8000";

/** Prefijo versionado del api-gateway para llamadas server-side (SSR, +server.js). */
export const API_V1 = `${INTERNAL_API_BASE}/api/v1`;

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
