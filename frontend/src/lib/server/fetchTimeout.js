// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Tiempo límite de respuesta para las llamadas del SSR al api-gateway y a Keycloak.
 *
 * **El problema.** Ni una sola de las ~190 llamadas server-side declaraba
 * `signal`. El `fetch` de Node no tiene tiempo límite por defecto: si el
 * gateway acepta la conexión y no responde —arrancando, saturado, o con un
 * upstream colgado— la petición del SSR espera **indefinidamente**. Cada
 * petición de usuario ocupa mientras tanto un hueco del bucle de eventos y una
 * conexión del pool de `undici`.
 *
 * El layout privado hace tres llamadas por navegación (`/auth/me`,
 * `/auth/context`, `/dependencias`), así que el frontend deja de responder
 * **entero** —incluida la pantalla de login, que no depende del gateway— por un
 * fallo que el gateway ya sabe acotar: su propio `proxy_timeout` es de 30 s.
 * Sin este tope, el frontend es el eslabón que convierte una degradación del
 * backend en una caída total.
 *
 * **Qué se acota: la RESPUESTA, no la descarga.** El temporizador se cancela en
 * cuanto llegan las cabeceras. Un `AbortSignal.timeout` corriente abortaría la
 * petición completa, cuerpo incluido, y eso rompería las descargas legítimas
 * que el sistema sí produce: el ZIP de exportación de un expediente, el AIP, el
 * FUID, los CSV de reportes. Ninguno de esos tiene un tamaño acotado y varios
 * tardan minutos en un fondo grande. Lo que se quiere impedir es un upstream
 * que **no contesta**, no uno que contesta despacio porque está enviando 800 MB.
 *
 * (Contrapartida asumida y explícita: un cuerpo que se detiene a la mitad no lo
 * corta nadie. Acotarlo exigiría `bodyTimeout` del dispatcher de `undici`, que
 * no se expone por `fetch` estándar; y el remedio de matar por tiempo total
 * sería peor que la enfermedad.)
 *
 * **Quien necesita más, lo pide.** Si la llamada trae su propio `signal`, el
 * envoltorio no lo toca. Es la vía para el asistente conversacional, cuyo bucle
 * LLM el gateway acota aparte en 180 s.
 */

/** Tiempo por defecto, en milisegundos. Alineado con `proxy_timeout` del gateway. */
export const TIEMPO_LIMITE_POR_DEFECTO = 30_000;

let instalado = false;

/**
 * Envuelve `globalThis.fetch` una sola vez para acotar la espera de respuesta.
 *
 * Idempotente: llamarlo dos veces no encadena dos envoltorios.
 *
 * @param {string[]} prefijos URL cuyo comienzo activa el tope (gateway, Keycloak).
 * @param {number} [ms] Milisegundos de espera de cabeceras.
 */
export function instalarTiempoLimite(prefijos, ms = TIEMPO_LIMITE_POR_DEFECTO) {
  if (instalado) return;
  instalado = true;

  const original = globalThis.fetch;

  globalThis.fetch = async function (entrada, init) {
    const url = typeof entrada === "string" ? entrada : (entrada?.url ?? "");

    // Con `signal` propio manda quien llama; fuera de los prefijos, no es
    // tráfico nuestro y no nos toca ponerle plazo.
    if (init?.signal || !prefijos.some((p) => url.startsWith(p))) {
      return original(entrada, init);
    }

    const control = new AbortController();
    const temporizador = setTimeout(() => {
      control.abort(new DOMException(`Sin respuesta en ${ms} ms`, "TimeoutError"));
    }, ms);

    try {
      return await original(entrada, { ...init, signal: control.signal });
    } finally {
      // Clave: se cancela al llegar las CABECERAS. A partir de aquí el cuerpo
      // se descarga sin plazo — ver la nota sobre descargas arriba.
      clearTimeout(temporizador);
    }
  };
}

/** Solo para tests: permite reinstalar el envoltorio sobre un `fetch` nuevo. */
export function _reiniciarParaTests() {
  instalado = false;
}
