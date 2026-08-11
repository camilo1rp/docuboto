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

import { API_V1, KEYCLOAK_INTERNAL_BASE } from "$lib/utils/config.server.js";
import { conIpDelCliente, instalarPropagacionDeIp } from "$lib/server/clientIp.js";
import { instalarTiempoLimite } from "$lib/server/fetchTimeout.js";
import { aplicarCabecerasDeSeguridad } from "$lib/server/securityHeaders.js";
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

  // Tiempo límite de RESPUESTA para gateway y Keycloak (ver
  // $lib/server/fetchTimeout.js). Sin él, un upstream que acepta la conexión y
  // no contesta cuelga la petición del SSR para siempre, y el layout privado
  // hace tres llamadas por navegación: el frontend entero deja de responder por
  // una degradación que el backend ya sabe acotar.
  instalarTiempoLimite([API_V1, KEYCLOAK_INTERNAL_BASE]);

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

  const response = await conIpDelCliente(ip, () => resolve(event));

  return aplicarCabecerasDeSeguridad(response);
}

/**
 * Manejo de errores inesperados del servidor.
 *
 * El comentario anterior decía «no filtrar stack traces al cliente» y acto
 * seguido devolvía `error.message` tal cual. El stack no viajaba, cierto, pero
 * el mensaje sí — y el mensaje de un fallo de red de Node es
 * `connect ECONNREFUSED 172.18.0.7:8080`. Eso es topología interna: nombres de
 * servicio, direcciones de la red Docker y puertos, servidos a cualquiera que
 * provoque un error. Con `undici` los mensajes llegan a incluir la URL completa
 * de la petición fallida, cabeceras incluidas en algunos casos.
 *
 * Ahora el cliente recibe un texto fijo más un identificador de correlación; el
 * detalle real queda solo en el log del servidor, junto a ese mismo
 * identificador. Quien atiende el incidente lo pide al usuario y lo busca en el
 * log — sin que el mensaje de error sea el canal.
 *
 * @type {import('@sveltejs/kit').HandleServerError}
 */
export function handleError({ error, event }) {
  // Identificador corto: suficiente para localizar la línea en el log y sin
  // valor para quien no tenga acceso a él.
  const referencia = crypto.randomUUID().slice(0, 8);

  console.error(`[hooks.server] [${referencia}] Error en ${event.url.pathname}:`, error);

  // App.Error está declarado en src/app.d.ts con { message, code? }
  return {
    message: "Ocurrió un error inesperado. El equipo técnico ya tiene el registro.",
    code: "INTERNAL_ERROR",
    referencia,
  };
}
