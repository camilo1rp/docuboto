// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * legalHold.js — Retenciones legales por motivo de un expediente (C-5 / C7).
 *
 * Un expediente puede tener varias retenciones a la vez (judicial + valoración)
 * y levantar una NO libera las demás. `valoracion_pendiente` la fija y la
 * levanta el sistema con la valoración del Comité sobre la TVD: la pantalla la
 * muestra pero no ofrece operarla.
 */

/** Motivos que una persona puede fijar (catálogo cerrado del backend). */
export const MOTIVOS_MANUALES = [
  "judicial",
  "disciplinario",
  "fiscal",
  "administrativo",
  "ddhh_memoria_historica",
];

/**
 * Longitud mínima del fundamento de LEVANTAR una retención por DDHH / memoria histórica
 * (Ley 1448/2011 art. 144 [confirmar]): solo la levanta un acto de autoridad competente,
 * explicado con holgura. Espejo de `FUNDAMENTO_MIN_LEVANTAR_DDHH` (archive-service).
 */
export const FUNDAMENTO_MIN_LEVANTAR_DDHH = 50;

/** @type {Record<string,string>} */
export const MOTIVO_LABELS = {
  judicial: "Judicial",
  disciplinario: "Disciplinario",
  fiscal: "Fiscal",
  administrativo: "Administrativo",
  ddhh_memoria_historica: "DDHH / memoria histórica",
  valoracion_pendiente: "Valoración pendiente (TVD)",
};

/** @param {string} m */
export function motivoLabel(m) {
  return MOTIVO_LABELS[m] ?? m;
}

/** La retención de sistema no se opera a mano. @param {any} hold */
export function esDeSistema(hold) {
  return hold?.motivo_tipo === "valoracion_pendiente";
}

/** @param {any} hold */
export function estaActivo(hold) {
  return !hold?.levantado_at;
}

/**
 * Cuerpo del POST de fijar. Allowlist; `valoracion_pendiente` no se admite.
 * @param {any} f
 * @returns {{ok: Record<string,string>} | {error: string}}
 */
export function construirFijar(f) {
  const motivo = f?.motivo_tipo;
  if (!MOTIVOS_MANUALES.includes(motivo)) return { error: "Elija el motivo de la retención." };
  const fundamento = String(f?.fundamento ?? "").trim();
  if (fundamento.length < 10) return { error: "El fundamento debe tener al menos 10 caracteres." };
  const acta = String(f?.acta_ref ?? "").trim();
  if (!acta) return { error: "Indique la referencia del acta u oficio que ordena la retención." };
  return { ok: { motivo_tipo: motivo, fundamento, acta_ref: acta } };
}

/**
 * Cuerpo del POST de levantar. El `motivo_tipo` es el que la persona CONFIRMÓ,
 * no el de la fila: el backend exige que coincidan (409 `motivo_no_coincide`).
 * @param {any} f
 * @returns {{ok: Record<string,string>} | {error: string}}
 */
export function construirLevantar(f) {
  const confirmado = f?.motivo_confirmado;
  if (!MOTIVOS_MANUALES.includes(confirmado)) {
    return { error: "Confirme el motivo de la retención que va a levantar." };
  }
  const fundamento = String(f?.fundamento ?? "").trim();
  if (fundamento.length < 10) return { error: "El fundamento debe tener al menos 10 caracteres." };
  if (confirmado === "ddhh_memoria_historica" && fundamento.length < FUNDAMENTO_MIN_LEVANTAR_DDHH) {
    return {
      error: `Levantar una retención por DDHH / memoria histórica exige el acto de la autoridad competente explicado en al menos ${FUNDAMENTO_MIN_LEVANTAR_DDHH} caracteres.`,
    };
  }
  const acta = String(f?.acta_ref ?? "").trim();
  if (!acta)
    return { error: "Indique la referencia del acta u oficio que ordena el levantamiento." };
  return { ok: { motivo_tipo: confirmado, fundamento, acta_ref: acta } };
}
