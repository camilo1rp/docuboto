// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Propagación de la IP del navegador desde el SSR hacia el api-gateway.
 *
 * **El problema.** Cada llamada del SSR al gateway abre una conexión NUEVA desde
 * el contenedor del frontend, así que el gateway ve siempre la misma dirección:
 * la del contenedor. Como su limitador de tasa cuenta por IP, **todo el tráfico
 * de todos los usuarios comparte un único cubo** de 100 peticiones por minuto —
 * y una sola pantalla del panel dispara varias llamadas. El resultado no es un
 * ataque frustrado: son 429 generalizados en una mañana normal de ventanilla.
 * Y la IP que acaba en el `audit_log` es la del contenedor, no la de nadie.
 *
 * **Por qué aquí y no en cada proxy.** Los ~40 `+server.js` usan el `fetch`
 * global, no el `event.fetch` de SvelteKit, así que `handleFetch` no los
 * intercepta. Añadir la cabecera archivo por archivo sería una lista que hay que
 * recordar — y la lista que hay que recordar es la que se olvida: el proxy nuevo
 * que alguien escriba dentro de seis meses nacería sin ella, en silencio.
 *
 * Se resuelve envolviendo `fetch` **una vez**, con la IP guardada en un
 * `AsyncLocalStorage` por petición. Es la herramienta de Node para contexto
 * transversal y no requiere que ningún proxy sepa que esto existe.
 *
 * **Solo hacia el gateway.** La cabecera se añade únicamente a las URL que
 * apuntan al API interno. Una llamada a Keycloak o a cualquier otro destino sale
 * intacta: propagar la IP del usuario a terceros sería una fuga, no una mejora.
 */

import { AsyncLocalStorage } from "node:async_hooks";

/** @type {AsyncLocalStorage<{ ip: string }>} */
const contexto = new AsyncLocalStorage();

let instalado = false;

/**
 * Ejecuta `fn` con la IP del cliente disponible para el `fetch` envuelto.
 * @template T
 * @param {string} ip
 * @param {() => T} fn
 * @returns {T}
 */
export function conIpDelCliente(ip, fn) {
  return contexto.run({ ip }, fn);
}

/**
 * Envuelve `globalThis.fetch` una sola vez para que añada `X-Forwarded-For`
 * a las llamadas dirigidas a `apiBase`.
 *
 * Idempotente: llamarlo dos veces no encadena dos envoltorios.
 * @param {string} apiBase Prefijo de las URL del api-gateway.
 */
export function instalarPropagacionDeIp(apiBase) {
  if (instalado) return;
  instalado = true;

  const original = globalThis.fetch;

  globalThis.fetch = function (entrada, init) {
    const almacen = contexto.getStore();
    const url = typeof entrada === "string" ? entrada : (entrada?.url ?? "");

    if (!almacen?.ip || !url.startsWith(apiBase)) {
      return original(entrada, init);
    }

    const cabeceras = new Headers(
      init?.headers ?? (entrada instanceof Request ? entrada.headers : undefined),
    );
    // Se AÑADE al final de la cadena, no se sobrescribe: `X-Forwarded-For` es una
    // lista de saltos y el gateway la recorre desde la derecha descartando los
    // suyos. Reemplazarla borraría la información del proxy que hay delante.
    const previo = cabeceras.get("x-forwarded-for");
    cabeceras.set("x-forwarded-for", previo ? `${previo}, ${almacen.ip}` : almacen.ip);

    return original(entrada, { ...init, headers: cabeceras });
  };
}
