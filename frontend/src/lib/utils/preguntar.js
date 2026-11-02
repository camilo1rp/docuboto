// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * preguntar.js — texto y enlaces del panel «Preguntar» (ADR-045 §3 y §5).
 *
 * El aviso nombra al proveedor, el alojamiento, los modelos y los subencargados
 * tal como los declara la pasarela. Si falta alguno de esos datos, no hay aviso:
 * el panel no se ofrece. El enlace de una cita apunta al anexo y a la página
 * cuando el anexo está en el radicado abierto; si no, al radicado citado.
 */

const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const ALOJAMIENTO = {
  installation: "de la instalación",
  hosted: "alojada",
};

/**
 * @param {string[]} partes
 * @returns {string}
 */
function enumerar(partes) {
  if (partes.length <= 1) return partes[0] ?? "";
  return `${partes.slice(0, -1).join(", ")} y ${partes[partes.length - 1]}`;
}

/**
 * @param {unknown} valor
 * @returns {valor is string}
 */
function texto(valor) {
  return typeof valor === "string" && valor.trim().length > 0;
}

/**
 * Aviso que se muestra antes de enviar la pregunta.
 *
 * @param {{id?: unknown, name?: unknown}|null|undefined} proveedor
 * @param {object|null|undefined} ruta
 *   `hosting`, `model_aliases` y `subprocessors` tal como los da la pasarela.
 * @param {string} nivel etiqueta del nivel del radicado (Pública, Reservada, Clasificada)
 * @returns {string|null}
 */
export function avisoTransmision(proveedor, ruta, nivel) {
  const donde = ALOJAMIENTO[/** @type {string} */ (ruta?.hosting)];
  const alias = Array.isArray(ruta?.model_aliases) ? ruta.model_aliases.filter(texto) : [];
  if (!donde || alias.length === 0 || !texto(nivel)) return null;
  if (!texto(proveedor?.name) || !texto(proveedor?.id)) return null;
  const subs = (Array.isArray(ruta?.subprocessors) ? ruta.subprocessors : [])
    .filter((s) => texto(s?.name) && texto(s?.id))
    .map((s) => `${s.name} (${s.id})`);
  const cola = subs.length ? ` Los subencargados de esta ruta son ${enumerar(subs)}.` : "";
  return (
    `La pregunta y este radicado (${nivel}) se transmiten a ` +
    `${proveedor.name} (${proveedor.id}) por la ruta ${donde}. ` +
    `Los modelos de esta ruta son ${enumerar(alias)}.${cola}`
  );
}

/**
 * @param {object} cita
 * @param {string} radicadoId
 * @param {Array<{id?: string, file_id?: string, filename?: string}>} anexos
 * @returns {{href: string, nombre: string}|null}
 */
export function enlaceCita(cita, radicadoId, anexos) {
  const pagina = Number.isInteger(cita?.page) && cita.page >= 1 ? cita.page : null;
  const anexo = (anexos ?? []).find((a) => a?.id && a.id === cita?.attachment_ref);
  if (anexo && UUID.test(radicadoId) && UUID.test(anexo.file_id ?? "")) {
    const base = `/bandeja/api/${radicadoId}/anexos/${anexo.file_id}`;
    const nombre = anexo.filename || "Anexo";
    return {
      href: pagina ? `${base}#page=${pagina}` : base,
      nombre: pagina ? `${nombre}, página ${pagina}` : nombre,
    };
  }
  if (texto(cita?.record_id) && UUID.test(cita.record_id)) {
    const ref = texto(cita.source_ref) ? cita.source_ref : "Radicado";
    return {
      href: `/bandeja?radicado=${cita.record_id}`,
      nombre: pagina ? `${ref}, página ${pagina}` : ref,
    };
  }
  return null;
}
