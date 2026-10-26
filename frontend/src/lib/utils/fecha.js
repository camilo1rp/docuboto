// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Formateo de fechas — y por qué no basta con `new Date(iso).toLocaleDateString`.
 *
 * La API devuelve dos cosas distintas que se parecen mucho:
 *
 *   - **Instantes** (`2026-08-19T12:53:11+00:00`): un punto en el tiempo. Llevan
 *     zona, y convertirlos a la del lector es exactamente lo que hay que hacer.
 *   - **Fechas puras** (`2026-09-19`): un día del calendario, sin hora. El plazo
 *     de un préstamo, el fin del archivo de gestión, la vigencia de una TRD, las
 *     fechas extremas de un expediente.
 *
 * `new Date("2026-09-19")` interpreta la segunda como **medianoche UTC**. Al
 * formatearla en la zona del navegador, en Colombia (UTC−5) retrocede al día
 * anterior. Un préstamo pactado al 19 se mostraba «18/09/2026»; un plazo de
 * retención vence, en pantalla, un día antes que en la ley. El error es
 * sistemático y silencioso: en el país al que va dirigido el sistema, siempre
 * está mal, y no hay nada en la pantalla que lo delate.
 *
 * Una fecha pura no tiene zona horaria que convertir. Aquí se formatea por sus
 * componentes, tal cual la escribió el servidor.
 *
 * `shared/tests`… no: el guardián de esta regla es
 * `fecha-unico-formateador.test.js`, que impide que vuelva a aparecer un
 * `toLocaleDateString` suelto en otra pantalla.
 */

const SOLO_FECHA = /^(\d{4})-(\d{2})-(\d{2})$/;
const LOCALE = "es-CO";

const MESES_CORTOS = [
  "ene",
  "feb",
  "mar",
  "abr",
  "may",
  "jun",
  "jul",
  "ago",
  "sep",
  "oct",
  "nov",
  "dic",
];

const MESES = [
  "enero",
  "febrero",
  "marzo",
  "abril",
  "mayo",
  "junio",
  "julio",
  "agosto",
  "septiembre",
  "octubre",
  "noviembre",
  "diciembre",
];

/**
 * Día del calendario. Nunca desplaza una fecha pura.
 *
 * @param {string|Date|null|undefined} valor
 * @param {{ estilo?: "corto"|"medio"|"largo" }} [opciones]
 *   - `corto` (por defecto): `19/09/2026`
 *   - `medio`: `19 sep 2026`
 *   - `largo`: `19 de septiembre de 2026`
 * @returns {string} la fecha formateada, o `—` si no hay valor
 */
export function formatearFecha(valor, opciones = {}) {
  if (!valor) return "—";
  const estilo = opciones.estilo ?? "corto";

  const puro = typeof valor === "string" ? SOLO_FECHA.exec(valor.trim()) : null;
  if (puro) {
    const [, anio, mes, dia] = puro;
    if (estilo === "largo") return `${Number(dia)} de ${MESES[Number(mes) - 1]} de ${anio}`;
    if (estilo === "medio") return `${Number(dia)} ${MESES_CORTOS[Number(mes) - 1]} ${anio}`;
    return `${dia}/${mes}/${anio}`;
  }

  const d = valor instanceof Date ? valor : new Date(valor);
  if (Number.isNaN(d.getTime())) return String(valor);
  const formato =
    estilo === "largo"
      ? { year: "numeric", month: "long", day: "2-digit" }
      : estilo === "medio"
        ? { year: "numeric", month: "short", day: "numeric" }
        : { year: "numeric", month: "2-digit", day: "2-digit" };
  return d.toLocaleDateString(LOCALE, /** @type {Intl.DateTimeFormatOptions} */ (formato));
}

/**
 * Instante con hora. Para lo que ocurrió en un momento concreto: un asiento de
 * auditoría, la firma de un documento, el registro de un radicado.
 *
 * Una fecha pura no tiene hora que mostrar, así que se delega en
 * `formatearFecha` en vez de inventar las 00:00 — que además serían las del día
 * equivocado.
 *
 * @param {string|Date|null|undefined} valor
 * @returns {string}
 */
export function formatearFechaHora(valor) {
  if (!valor) return "—";
  if (typeof valor === "string" && SOLO_FECHA.test(valor.trim())) {
    return formatearFecha(valor);
  }
  const d = valor instanceof Date ? valor : new Date(valor);
  if (Number.isNaN(d.getTime())) return String(valor);
  // Componentes explícitos, no `dateStyle: "short"`: ese estilo abrevia el año
  // a dos cifras («19/08/26»), y un año ambiguo en un asiento de auditoría o en
  // la fecha de una firma no es una abreviatura, es una pérdida de dato.
  return d.toLocaleString(LOCALE, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
