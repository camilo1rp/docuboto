// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * valoracion.js — Valoración del Comité sobre una TVD (C-5 / C7).
 * Solo una TVD aprobada o convalidada admite la valoración (el backend responde
 * 409 `tvd_no_aprobada` en otro caso; la UI no ofrece el botón).
 */

/** @param {any} item */
export function admiteValoracion(item) {
  return (
    item?.tipo_instrumento !== "TRD" && ["aprobada", "convalidada"].includes(item?.estado ?? "")
  );
}

const FECHA_RE = /^\d{4}-\d{2}-\d{2}$/;

/**
 * ¿La disposición de la TVD es Selección (S)? Con ella la valoración no levanta el hold de todo
 * el fondo: cada expediente espera su acto de selección y el Comité declara el método de
 * muestreo. Acepta el código AGN y el alias heredado.
 * @param {any} item
 */
export function esSeleccion(item) {
  return ["S", "select"].includes(String(item?.disposition ?? item?.disposicion ?? ""));
}

/**
 * @param {any} f {acta_comite, fecha_acta, fundamento, metodo_muestreo?}
 * @param {boolean} [requiereMetodo] disposición Selección: el método de muestreo es obligatorio
 * @returns {{ok: Record<string,string>} | {error: string}}
 */
export function construirValoracion(f, requiereMetodo = false) {
  const acta = String(f?.acta_comite ?? "").trim();
  if (!acta) return { error: "Indique el acta del Comité que decide la valoración." };
  const fecha = String(f?.fecha_acta ?? "").trim();
  if (!FECHA_RE.test(fecha) || Number.isNaN(Date.parse(fecha))) {
    return { error: "La fecha del acta es obligatoria (AAAA-MM-DD)." };
  }
  const fundamento = String(f?.fundamento ?? "").trim();
  if (fundamento.length < 20) return { error: "El fundamento debe tener al menos 20 caracteres." };
  const metodo = String(f?.metodo_muestreo ?? "").trim();
  if (requiereMetodo && metodo.length < 10) {
    return { error: "Declare el método de muestreo (mínimo 10 caracteres)." };
  }
  if (metodo && metodo.length < 10) {
    return { error: "El método de muestreo debe tener al menos 10 caracteres." };
  }
  return {
    ok: {
      acta_comite: acta,
      fecha_acta: fecha,
      fundamento,
      ...(metodo ? { metodo_muestreo: metodo } : {}),
    },
  };
}

/**
 * Cuerpo del acto de selección de UN expediente (disposición S).
 * @param {any} f {accion, fundamento}
 * @returns {{ok: {accion: string, fundamento: string}} | {error: string}}
 */
export function construirSeleccion(f) {
  const accion = f?.accion;
  if (accion !== "conservar" && accion !== "descartar") {
    return { error: "Elija si el expediente se conserva o se descarta." };
  }
  const fundamento = String(f?.fundamento ?? "").trim();
  if (fundamento.length < 10) return { error: "El fundamento debe tener al menos 10 caracteres." };
  return { ok: { accion, fundamento } };
}

/**
 * Texto del estado del soporte de una constancia de firma inobtenible (C14).
 * `soporte_estado` puede llegar como cadena o como objeto `{estado, lote_codigo,
 * acta_sha256, fecha}`; se leen las dos formas.
 * @param {any} exc
 * @returns {{estado: string, texto: string} | null}
 */
export function soporteEstadoInfo(exc) {
  const raw = exc?.soporte_estado;
  if (!raw) return null;
  const estado = typeof raw === "string" ? raw : raw.estado;
  const lote =
    (typeof raw === "object" ? raw.lote_codigo : null) ?? exc.soporte_lote_codigo ?? null;
  const acta =
    (typeof raw === "object" ? raw.acta_sha256 : null) ?? exc.soporte_acta_sha256 ?? null;
  const loteTxt = lote ? ` (lote ${lote})` : "";
  if (estado === "vigente") {
    return { estado, texto: "Vigente: el anexo de soporte está protegido y no se puede borrar." };
  }
  if (estado === "baja_avalada") {
    return {
      estado,
      texto: `Baja avalada por el Comité${loteTxt}: el anexo se eliminará al ejecutar el lote.`,
    };
  }
  if (estado === "eliminado_por_trd") {
    return {
      estado,
      texto: `Soporte eliminado por disposición TRD${loteTxt}${acta ? `, acta ${acta}` : ""}. La constancia firmada se conserva.`,
    };
  }
  return null;
}
