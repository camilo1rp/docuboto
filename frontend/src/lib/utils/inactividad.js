// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Detector de INACTIVIDAD REAL de la persona.
 *
 * **Para qué existe, que no es lo que parece.** Esto NO implementa un cierre de
 * sesión por inactividad: ese control ya existe y vive donde debe, en Keycloak
 * (`ssoSessionIdleTimeout`, 30 min en producción), y un cierre decidido por el
 * navegador sería un control falso — se salta desactivando JavaScript.
 *
 * Lo que hace es **quitar de en medio lo que anulaba ese control**. La bandeja
 * de correo sondea cada 12 s mientras la pestaña esté abierta; cada sondeo pasa
 * por el `handle` del servidor, dispara el refresco transparente del token y
 * **reinicia el contador de inactividad de Keycloak**. Efecto: una pestaña
 * olvidada en una mesa mantenía la sesión viva hasta el tope absoluto (10 h),
 * con la pantalla desbloqueada. El servidor tenía la política correcta y el
 * cliente la desactivaba sin que nadie lo hubiera decidido.
 *
 * Así que aquí no se cierra nada: se **deja de fingir actividad**. Sin
 * interacción real, el sondeo para, no hay refresco, y el temporizador del
 * servidor hace su trabajo.
 *
 * `visibilitychange` cuenta como actividad SOLO al volver a mirar la pestaña:
 * ocultarla no es actividad, y un cambio a otra aplicación no debe renovar
 * nada.
 */

const EVENTOS = ["pointerdown", "keydown", "wheel", "touchstart"];

/**
 * @param {object} opts
 * @param {number} opts.msSinActividad  Umbral sin interacción real.
 * @param {() => void} opts.alInactivo  Se llama UNA vez al cruzar el umbral.
 * @param {() => void} opts.alActivo    Se llama al volver la actividad, solo si
 *                                      antes se había marcado inactivo.
 * @returns {() => void} función de limpieza
 */
export function vigilarInactividad({ msSinActividad, alInactivo, alActivo }) {
  // Guarda de SSR: este módulo lo importa una página, y las páginas se renderizan
  // también en el servidor, donde no existe `document`. Devolver una limpieza
  // vacía es más honesto que romper el render.
  if (typeof document === "undefined") return () => {};

  let temporizador = null;
  let inactivo = false;

  function marcarInactivo() {
    inactivo = true;
    alInactivo?.();
  }

  function reiniciar() {
    if (inactivo) {
      inactivo = false;
      alActivo?.();
    }
    if (temporizador) clearTimeout(temporizador);
    temporizador = setTimeout(marcarInactivo, msSinActividad);
  }

  function alCambiarVisibilidad() {
    // Volver a la pestaña es actividad; irse de ella NO lo es.
    if (document.visibilityState === "visible") reiniciar();
  }

  for (const evento of EVENTOS) {
    document.addEventListener(evento, reiniciar, { passive: true });
  }
  document.addEventListener("visibilitychange", alCambiarVisibilidad);
  reiniciar();

  return () => {
    if (temporizador) clearTimeout(temporizador);
    for (const evento of EVENTOS) document.removeEventListener(evento, reiniciar);
    document.removeEventListener("visibilitychange", alCambiarVisibilidad);
  };
}
