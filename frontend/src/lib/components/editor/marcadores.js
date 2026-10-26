// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * marcadores.js — Protege la sintaxis `*CAMPO*` de la cursiva del editor.
 *
 * El editor trae la regla de entrada de Markdown para cursiva —`*texto*` se
 * convierte en `<em>texto</em>` en cuanto se teclea el asterisco de cierre—, y
 * los marcadores de combinación se escriben exactamente así. El resultado, tal
 * y como apareció al usarlo: quien escribía `*NOM_R*` en una plantilla obtenía
 * `<em>NOM_R</em>` guardado en base de datos. Los asteriscos desaparecían antes
 * de que la plantilla llegara a guardarse siquiera.
 *
 * Y no fallaba a la vista: la plantilla se veía bien —en cursiva—, el borrador
 * salía con el texto plano, y como el marcador ya no tenía asteriscos, nada lo
 * delataba como un campo sin resolver. Ni la lista de `faltantes` podía avisar,
 * porque para el backend allí nunca hubo un marcador. Todo el mecanismo de
 * combinación de E23 era inalcanzable desde la única pantalla que escribe
 * plantillas.
 *
 * La regla de abajo casa con la MISMA forma que reconoce el backend
 * (`\*([A-Z][A-Z0-9_]{0,29})\*`) y, con prioridad por encima de la cursiva,
 * consume la entrada dejando el texto tal cual. La cursiva por asteriscos sigue
 * funcionando para cualquier otra cosa: `*importante*` no casa —lleva
 * minúsculas— y se sigue poniendo en cursiva.
 */

/** Misma forma que `_RE_LEGADO` en `document-service/app/core/combinacion.py`. */
const FORMA_MARCADOR = /(\*[A-Z][A-Z0-9_]{0,29}\*)$/;

/**
 * Crea la extensión que protege los marcadores.
 *
 * @param {any} core — módulo `@tiptap/core` ya importado (carga diferida)
 * @returns {any} extensión de TipTap
 */
export function crearProteccionDeMarcadores(core) {
  return core.Extension.create({
    name: "marcadoresDeCombinacion",
    // Por encima de la cursiva (100): TipTap ordena las reglas de entrada por
    // prioridad descendente y la primera que casa se queda con la pulsación.
    priority: 1000,

    addInputRules() {
      return [
        new core.InputRule({
          find: FORMA_MARCADOR,
          handler: ({ state, range, match }) => {
            // Reinsertar el mismo texto es lo que consume la regla: la cursiva
            // ya no llega a verla. `insertText` sin marcas deja el marcador en
            // texto plano, que es lo que el backend sabe leer.
            state.tr.insertText(match[0], range.from, range.to);
          },
        }),
      ];
    },
  });
}
