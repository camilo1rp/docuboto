// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
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
 */

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

  return response;
}
