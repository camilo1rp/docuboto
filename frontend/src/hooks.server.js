// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * hooks.server.js — Hooks de servidor de SvelteKit para OrpycaMCP.
 *
 * handle():
 *  - Intercepta todos los requests antes de que lleguen a las rutas.
 *  - Si el access_token está ausente pero hay refresh_token, intenta
 *    renovarlo de forma transparente antes de que el layout server
 *    tenga que redirigir a login.
 *  - Añade `event.locals.accessToken` y `event.locals.user` para que
 *    los load functions de las rutas los lean sin volver a parsear la cookie.
 *
 * handleError():
 *  - Registra errores inesperados; evita filtrar stack traces al cliente.
 */

import { API_V1 } from "$lib/utils/config.server.js";
import { conIpDelCliente, instalarPropagacionDeIp } from "$lib/server/clientIp.js";
import {
  COOKIE_ACCESS_TOKEN,
  COOKIE_REFRESH_TOKEN,
  refreshAccessToken,
  setSessionCookies,
  decodeJwt,
  extractSessionUser,
} from "$lib/services/auth.server.js";

/**
 * Hook principal: refresh transparente del access_token.
 * @type {import('@sveltejs/kit').Handle}
 */
export async function handle({ event, resolve }) {
  const { cookies } = event;

  // Propagación de la IP del navegador hacia el api-gateway (ver
  // $lib/server/clientIp.js). Sin esto, TODAS las llamadas del SSR llegan al
  // gateway con la dirección del contenedor del frontend y comparten un único
  // cubo de limitación de tasa — 429 generalizados en una mañana normal — y la
  // IP que acaba en el `audit_log` no es la de nadie.
  instalarPropagacionDeIp(API_V1);

  let accessToken = cookies.get(COOKIE_ACCESS_TOKEN);
  const refreshToken = cookies.get(COOKIE_REFRESH_TOKEN);

  // Refresh transparente: si no hay access_token pero hay refresh_token,
  // intentar renovar antes de pasar a la ruta
  if (!accessToken && refreshToken) {
    const tokenSet = await refreshAccessToken(refreshToken).catch(() => null);
    if (tokenSet?.access_token) {
      setSessionCookies(cookies, tokenSet);
      accessToken = tokenSet.access_token;
    }
  }

  // Poblar locals para evitar re-parseo en cada layout/page
  if (accessToken) {
    event.locals.accessToken = accessToken;
    const claims = decodeJwt(accessToken);
    if (claims) {
      event.locals.user = extractSessionUser(claims);
    }
  }

  // Todo lo que corra dentro de `resolve` —incluidos los ~40 proxies `+server.js`,
  // que usan el `fetch` global y por eso `handleFetch` no los alcanza— ve la IP
  // del cliente en el almacén asíncrono. Envolver aquí, y no en cada proxy, es lo
  // que evita que el próximo proxy que alguien escriba nazca sin la cabecera.
  let ip = "";
  try {
    ip = event.getClientAddress();
  } catch {
    // adapter-node lanza si `ADDRESS_HEADER` está configurado y la cabecera no
    // llegó. No es motivo para tumbar la petición: se sigue sin propagar IP,
    // que es el comportamiento anterior.
    ip = "";
  }

  return conIpDelCliente(ip, () => resolve(event));
}

/**
 * Manejo de errores inesperados del servidor.
 * @type {import('@sveltejs/kit').HandleServerError}
 */
export function handleError({ error, event }) {
  // No filtrar stack traces al cliente en producción
  const message = error instanceof Error ? error.message : "Error inesperado del servidor";

  console.error(`[hooks.server] Error en ${event.url.pathname}:`, error);

  // App.Error está declarado en src/app.d.ts con { message, code? }
  return { message, code: "INTERNAL_ERROR" };
}
