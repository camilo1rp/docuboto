// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Cabeceras de respuesta transversales del SSR.
 *
 * **`Cache-Control: no-store`.** Ninguna respuesta declaraba política de caché,
 * así que el navegador aplica la heurística: guarda el HTML y lo sirve desde
 * disco al pulsar Atrás. En una ventanilla de radicación —un puesto compartido,
 * usuarios que se turnan— eso significa que el siguiente funcionario pulsa
 * Atrás y ve la bandeja del anterior: números de radicado, asuntos y, según la
 * habilitación de quien estuvo antes, documentos clasificados que él no puede
 * consultar. El backend hace bien su trabajo (el `no-read-up` filtra en la
 * fuente) y aun así el dato se ve, porque no vuelve a pedirse.
 *
 * Cerrar sesión no lo arregla: el HTML ya está en el disco del navegador y el
 * historial sobrevive al logout.
 *
 * Se aplica al HTML y al JSON de los ~190 proxies, que es exactamente el
 * material de negocio. Los estáticos versionados (`_app/immutable/…`) no pasan
 * por aquí —`adapter-node` los sirve antes del handler— y además quedan fuera
 * por tipo de contenido: su caché larga es correcta y no se toca.
 *
 * Una respuesta que YA declara su propia política se respeta: hay proxies de
 * descarga que fijan la suya y no deben ser sobrescritos a ciegas.
 *
 * **El resto de cabeceras** son las mínimas que no dependen del despliegue.
 * `Strict-Transport-Security` no se emite aquí a propósito: depende de que el
 * sitio se sirva por TLS, y quien lo sabe es el proxy que lo termina, no el
 * proceso Node. Emitirla desde aquí en un despliegue de desarrollo por HTTP
 * dejaría el navegador clavado en HTTPS para ese host.
 *
 * La CSP no está aquí sino en `svelte.config.js` (`kit.csp`): SvelteKit inyecta
 * un script en línea con los datos de hidratación y necesita añadir su propio
 * `nonce` a la directiva. Escribir la cabecera a mano en este punto obligaría a
 * `unsafe-inline` en `script-src`, que es tanto como no tener CSP.
 *
 * **Excepción: `form-action`.** Esa directiva sí se completa aquí, y no por
 * gusto. `svelte.config.js` la declaraba como `'self'` con el comentario «el
 * logout y los formularios envían solo a este origen» — y era falso justamente
 * del logout: el cierre de sesión termina en el `end_session_endpoint` de
 * Keycloak, que es OTRO origen. Chrome aplica `form-action` a toda la cadena de
 * redirecciones del envío, así que bloqueaba el salto con
 * «Sending form data to '<URL>' violates ... form-action 'self'». Efecto visto
 * por quien lo usa: **el botón de salir no hace nada**. Y el fallo es peor que
 * inerte, porque el servidor SÍ había borrado ya las cookies: la sesión local
 * queda cerrada, la de Keycloak abierta, y la pantalla sigue mostrando al
 * usuario dentro hasta que algo la recarga.
 *
 * Se completa en RUNTIME, no en `svelte.config.js`, porque la URL pública de
 * Keycloak es `$env/dynamic/public`: un operador la cambia en el compose sin
 * reconstruir la imagen. Una CSP horneada en el build quedaría apuntando al
 * origen viejo y volvería a romper el cierre de sesión, esta vez en producción
 * y en silencio. Leerla de la MISMA fuente que construye la URL de logout es lo
 * que impide que las dos diverjan.
 */

import { env } from "$env/dynamic/public";

/**
 * Origen (esquema + host + puerto, sin ruta) al que el logout envía el
 * formulario. Cadena vacía si no está configurado: entonces no se amplía nada.
 * @returns {string}
 */
export function origenProveedorDeIdentidad() {
  const url = env.PUBLIC_KEYCLOAK_URL ?? "";
  if (!url) return "";
  try {
    return new URL(url).origin;
  } catch {
    return "";
  }
}

/**
 * Añade un origen a la directiva `form-action` de una CSP ya emitida,
 * conservando el resto intacto (incluido el `nonce` de SvelteKit).
 *
 * @param {string} csp — valor de la cabecera Content-Security-Policy
 * @param {string} origen
 * @returns {string} la política con la directiva ampliada
 */
export function ampliarFormAction(csp, origen) {
  if (!csp || !origen) return csp;
  const directivas = csp.split(";").map((d) => d.trim());
  let tocada = false;
  const salida = directivas.map((d) => {
    if (!/^form-action(\s|$)/i.test(d)) return d;
    tocada = true;
    return d.split(/\s+/).includes(origen) ? d : `${d} ${origen}`;
  });
  // Si la política no declaraba `form-action`, no se inventa una: sin la
  // directiva el navegador no restringe, y añadirla aquí sería endurecer por
  // sorpresa una política que alguien escribió a conciencia en otro sitio.
  return tocada ? salida.join("; ") : csp;
}

/** Tipos de contenido que llevan material de negocio y no deben quedar en caché. */
const TIPOS_SIN_CACHE = ["text/html", "application/json"];

/**
 * @param {Response} response
 * @returns {Response} la misma respuesta, con las cabeceras aplicadas.
 */
export function aplicarCabecerasDeSeguridad(response) {
  const tipo = response.headers.get("content-type") ?? "";

  if (!response.headers.has("cache-control") && TIPOS_SIN_CACHE.some((t) => tipo.includes(t))) {
    response.headers.set("cache-control", "no-store");
  }

  // El navegador no debe adivinar el tipo: un anexo servido por un proxy con un
  // tipo declarado no puede acabar interpretándose como HTML.
  response.headers.set("x-content-type-options", "nosniff");

  // Las URL de este sistema llevan números de radicado e identificadores de
  // expediente. No deben viajar como `Referer` a ningún tercero.
  response.headers.set("referrer-policy", "same-origin");

  // Redundante con `frame-ancestors` de la CSP, y a propósito: los navegadores
  // que no aplican la CSP sí honran esta.
  response.headers.set("x-frame-options", "DENY");

  // El sistema no usa ninguna de estas capacidades; negarlas evita que un
  // componente de terceros las estrene sin que nadie lo decida.
  response.headers.set("permissions-policy", "camera=(), microphone=(), geolocation=()");

  // El logout envía el formulario a Keycloak (ver la nota de arriba).
  const csp = response.headers.get("content-security-policy");
  if (csp) {
    const ampliada = ampliarFormAction(csp, origenProveedorDeIdentidad());
    if (ampliada !== csp) response.headers.set("content-security-policy", ampliada);
  }

  return response;
}
