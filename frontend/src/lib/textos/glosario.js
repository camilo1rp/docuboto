// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * glosario.js — Cómo habla la interfaz (ADR-057 §7, CR-001 plan §1.5).
 *
 * Una sola fuente para tres cosas:
 *
 * - `TERMINOS`: un concepto, una palabra. Cada término dice su uso y las
 *   variantes que no se usan en pantalla.
 * - `PATRONES_PROHIBIDOS`: códigos internos y jerga que no pueden llegar al
 *   texto visible (`sin-codigos-en-pantalla.test.js`).
 * - `MARCADORES_TUTEO`: formas de tú que delatan un texto que no está en usted
 *   (`usted-siempre.test.js`).
 *
 * Un término nuevo entra aquí en el mismo PR que lo usa por primera vez.
 */

/**
 * @typedef {Object} Termino
 * @property {string} termino      Palabra que se usa en pantalla.
 * @property {string} uso          Cuándo se usa.
 * @property {string[]} [evitar]   Variantes que no se usan para ese concepto.
 */

/** @type {Termino[]} */
export const TERMINOS = [
  { termino: "radicado", uso: "El registro con número.", evitar: ["radicación (como objeto)"] },
  { termino: "documento", uso: "El archivo o el papel.", evitar: ["adjunto (como el principal)"] },
  {
    termino: "dependencia",
    uso: "La oficina de la entidad.",
    evitar: ["área", "unidad", "tenant"],
  },
  { termino: "trasladar", uso: "Mover un radicado a otra dependencia." },
  {
    termino: "devolver",
    uso: "Regresar un radicado al paso o a la persona anterior.",
    evitar: ["revertir"],
  },
  { termino: "reasignar", uso: "Cambiar a la persona responsable dentro del mismo paso." },
  { termino: "finalizar el trámite", uso: "Terminar la atención de un radicado." },
  { termino: "cerrar un expediente", uso: "Terminar un expediente de archivo." },
  { termino: "remitente", uso: "Quien envía una entrada." },
  { termino: "destinatario", uso: "A quien va una salida." },
  { termino: "peticionario", uso: "Quien presenta una PQRSD." },
  {
    termino: "visto bueno",
    uso: "Aprobación antes de firmar.",
    evitar: ["VoBo", "aprobación técnica"],
  },
  { termino: "vence el…", uso: "La fecha en que termina el plazo." },
  { termino: "plazo", uso: "Los días hábiles para responder." },
  { termino: "folios", uso: "Las hojas del documento en papel." },
  { termino: "nivel de acceso", uso: "Lo que una persona puede ver." },
  { termino: "nivel de reserva", uso: "Lo protegido que está un documento." },
  {
    termino: "huella del archivo",
    uso: "Lo que permite comprobar que el archivo no cambió.",
    evitar: ["checksum", "hash", "SHA-256"],
  },
  {
    termino: "lectura del documento",
    uso: "Cuando el sistema extrae datos del archivo.",
    evitar: ["ingesta", "indexado"],
  },
];

/**
 * Códigos internos y jerga que no pueden verse en pantalla (ADR-057 §7).
 * Las expresiones se prueban sobre el texto visible, no sobre el código.
 *
 * @type {{ patron: RegExp, motivo: string }[]}
 */
export const PATRONES_PROHIBIDOS = [
  { patron: /\bPERM_[A-Z_]+/, motivo: "código de permiso" },
  { patron: /\bUSUA_[A-Z_]+/, motivo: "código de permiso" },
  { patron: /\bRF-[A-Z]/, motivo: "código de requisito" },
  { patron: /\bADR-\d/, motivo: "decisión de arquitectura" },
  { patron: /\btenants?\b/i, motivo: "jerga: use «entidad»" },
  { patron: /\bchecksum\b/i, motivo: "jerga: use «huella del archivo»" },
  { patron: /\bingesta\b/i, motivo: "jerga: use «lectura del documento»" },
  { patron: /\bindexad[oa]s?\b/i, motivo: "jerga: use «leído»" },
  { patron: /\bJSON\b/, motivo: "formato interno" },
  { patron: /\bHTTP\b/, motivo: "protocolo interno" },
];

/**
 * Formas de tú. Solo las que no se confunden con la tercera persona: «sube» o
 * «revisa» pueden ser de usted o de él en una frase como «el sistema revisa…»,
 * así que entran solo los verbos y pronombres que no tienen esa lectura.
 */
export const MARCADORES_TUTEO = [
  // Pronombres y posesivos.
  "tú",
  "tu",
  "tus",
  "te",
  "ti",
  "contigo",
  // Segunda persona del presente, del futuro y del subjuntivo.
  "puedes",
  "debes",
  "tienes",
  "quieres",
  "necesitas",
  "estás",
  "eres",
  "has",
  "vas",
  "empiezas",
  "prefieres",
  "deseas",
  "sabes",
  "buscas",
  "desmarcas",
  "verás",
  "podrás",
  "tendrás",
  "recibirás",
  "puedas",
  "tengas",
  "quieras",
  "uses",
  "elijas",
  "indiques",
  // Imperativos de tú cuyo de usted es otra palabra (suba, escriba, elija…).
  // No entran «deja», «sigue», «usa», «cambia», «confirma» ni «revisa»: en la
  // interfaz casi siempre son tercera persona («el radicado no cambia»,
  // «se revisa») o usted en pregunta («¿Confirma…?»).
  "haz",
  "sube",
  "escribe",
  "elige",
  "selecciona",
  "llena",
  "arrastra",
  "pulsa",
  "presiona",
  "ingresa",
  "introduce",
  "intenta",
  "verifica",
  "busca",
  "añade",
  "asegúrate",
  "inténtalo",
  "complétalo",
  "comunícate",
  "recuerda",
];
