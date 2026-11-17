// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Verificación pública de una firma (ADR-054 §2): forma del código y textos.
 *
 * El código de una firma es su `firma_id`, un UUID de 128 bits: no es adivinable y
 * no revela nada del contenido (la verificación por hash del documento se descartó
 * en el ADR porque permitiría confirmar por fuerza bruta un documento reservado).
 */

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @param {unknown} codigo */
export function esCodigoDeFirma(codigo) {
  return typeof codigo === "string" && UUID_RE.test(codigo.trim());
}

/**
 * Veredicto en palabras. Una constancia de firma simple NO se reporta válida ni
 * inválida: un tercero no puede verificarla, y decir cualquiera de las dos cosas
 * sería falso.
 *
 * @param {import('$lib/types/api.js').PublicSignatureVerification} v
 * @returns {{tono: 'ok'|'mal'|'neutro', titulo: string}}
 */
export function veredicto(v) {
  if (v.kind !== "electronic_signature") {
    return {
      tono: "neutro",
      titulo: "Constancia de firma simple: no es una firma electrónica verificable por terceros",
    };
  }
  if (v.valida === true) return { tono: "ok", titulo: "Firma electrónica válida" };
  return { tono: "mal", titulo: "La firma electrónica NO es válida" };
}

/** Motivos del backend en palabras (vocabulario de `signature-service`). */
export const MOTIVOS_VERIFICACION = /** @type {Record<string,string>} */ ({
  constancia_firma_simple_no_verificable_por_terceros:
    "El sistema registró esta constancia con su propia clave; la puede comprobar la entidad, no un tercero.",
  xml_firmado_no_disponible:
    "El documento firmado de esta firma no está disponible para comprobarla.",
  documento_no_corresponde_a_firma_id: "El documento firmado no corresponde a esta firma.",
  firma_sin_huella_de_certificado_registrada: "La firma no tiene registrado su certificado.",
  certificado_revocado: "El certificado del firmante está revocado.",
  certificado_desconocido: "El certificado del firmante no se reconoce.",
  clave_comprometida: "La clave del firmante se declaró comprometida.",
  revocacion_no_verificable: "No se pudo consultar si el certificado del firmante está revocado.",
  instante_firma_no_cierto:
    "La firma no tiene fecha cierta para descartar la revocación posterior.",
});

/** @param {string|null|undefined} motivo */
export function motivoEnPalabras(motivo) {
  if (!motivo) return null;
  return MOTIVOS_VERIFICACION[motivo] ?? `Motivo técnico: ${motivo}`;
}
