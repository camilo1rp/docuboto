// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
/**
 * sellos.js — textos en español de la faceta de sellos de archivo (C-6, ADR-042).
 * El servidor emite códigos; la persona lee frases.
 */

/** Situaciones filtrables (valores EXACTOS del backend). */
export const SITUACIONES = [
  "por_vencer",
  "vencido",
  "sin_revocacion",
  "pendiente_worm",
  "rechazado",
];

/** @type {Record<string,string>} */
export const SITUACION_LABELS = {
  por_vencer: "Por vencer",
  vencido: "Vencido",
  sin_revocacion: "Sin revocación de la TSA",
  pendiente_worm: "Pendiente de WORM",
  rechazado: "Rechazado",
};

/** @type {Record<string,string>} */
export const ESTADO_LABELS = {
  vigente: "Vigente",
  pendiente_worm: "Pendiente de WORM",
  caducado: "Caducado",
};

/** @type {Record<string,string>} */
export const FUENTE_REVOCACION_LABELS = {
  crl: "CRL",
  ocsp: "OCSP",
  crl_ocsp: "CRL y OCSP",
  autofirmada: "Autofirmada (sin revocación)",
  ninguna: "Ninguna",
};

/** @type {Record<string,string>} */
export const OBJETO_TIPO_LABELS = {
  indice: "Índice electrónico",
  acta_transferencia: "Acta de transferencia",
  aip: "Paquete AIP",
};

/**
 * Texto del último aviso emitido para el sello. `rechazada:<motivo>` lleva el
 * motivo tras los dos puntos; uno desconocido se muestra tal cual (no se pierde).
 * @param {string|null|undefined} aviso
 * @returns {string}
 */
export function textoUltimoAviso(aviso) {
  if (!aviso) return "—";
  if (aviso.startsWith("rechazada:")) {
    const motivo = aviso.slice("rechazada:".length).trim();
    return motivo ? `Renovación rechazada: ${motivo}` : "Renovación rechazada";
  }
  switch (aviso) {
    case "tsa_por_vencer":
      return "Aviso: el certificado de la TSA está por vencer";
    case "sin_revocacion_tsa":
      return "Aviso: no se pudo comprobar la revocación de la TSA";
    case "tsa_revocada":
      return "Alerta: la TSA fue revocada";
    default:
      return aviso;
  }
}

/**
 * Nombre del objeto de la fila. Un objeto reservado NO se oculta ni se nombra:
 * la fila existe (el sello hay que vigilarlo) pero su referencia no se revela.
 * @param {{ objeto_reservado?: boolean, objeto_tipo?: string|null, objeto_id?: string|null }} fila
 */
export function textoObjeto(fila) {
  if (fila.objeto_reservado) return "Reservado";
  const tipo = fila.objeto_tipo ? (OBJETO_TIPO_LABELS[fila.objeto_tipo] ?? fila.objeto_tipo) : null;
  if (!tipo) return "—";
  return fila.objeto_id ? `${tipo} · ${fila.objeto_id}` : tipo;
}

/**
 * Días restantes en lenguaje llano; negativo = ya venció.
 * @param {number|null|undefined} dias
 */
export function textoDias(dias) {
  if (dias == null) return "—";
  if (dias < 0) return `Venció hace ${Math.abs(dias)} día${Math.abs(dias) === 1 ? "" : "s"}`;
  if (dias === 0) return "Vence hoy";
  return `${dias} día${dias === 1 ? "" : "s"}`;
}

/** Huella abreviada para mostrar (la completa va en el title). */
export function huellaCorta(fp) {
  if (!fp) return "—";
  return fp.length > 16 ? `${fp.slice(0, 8)}…${fp.slice(-8)}` : fp;
}
