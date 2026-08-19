// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Cómo se nombra una serie TRD en pantalla.
 *
 * Una serie tiene un código que la entidad usa a diario (`DEM-100`) y un nombre
 * («Derechos de Petición»). Su UUID no es ninguna de las dos cosas: no se puede
 * buscar en la TRD impresa, ni citar en un correo, ni reconocer de un vistazo.
 * Aun así la ficha del expediente lo pintaba en crudo, mientras el servidor ya
 * resolvía la serie completa para el drawer de disposición.
 */

/**
 * @param {{code?: string, name?: string}|null|undefined} serie
 * @returns {string|null} «CÓDIGO — Nombre», o lo que haya de los dos; `null` si
 *   no hay nada que mostrar (entonces quien llama decide qué hacer — la ficha
 *   cae al UUID, que es feo pero cierto).
 */
export function nombreSerieTrd(serie) {
  if (!serie) return null;
  const partes = [serie.code, serie.name].filter((p) => typeof p === "string" && p.trim());
  return partes.length ? partes.join(" — ") : null;
}

/**
 * Orden de preferencia por estado del instrumento: lo que RIGE va primero.
 * Un estado desconocido cae al final, no en medio de las vigentes.
 */
const ORDEN_ESTADO = { convalidada: 0, aprobada: 1, borrador: 2, migrada: 3, derogada: 4 };

/** Cómo se dice cada estado a quien clasifica. */
const ESTADO_TEXTO = {
  convalidada: "vigente",
  aprobada: "aprobada, sin convalidar",
  borrador: "borrador",
  migrada: "migrada",
  derogada: "derogada",
};

/**
 * Opciones de un desplegable de series TRD, con la versión y el estado a la
 * vista y las vigentes arriba.
 *
 * Una serie puede tener varias versiones al mismo tiempo: el circuito de
 * convalidación abre una v2 en borrador mientras la v1 convalidada sigue
 * rigiendo. Sin versión ni estado en la etiqueta, el desplegable muestra DOS
 * OPCIONES IDÉNTICAS —«DEM-100 — Derechos de Petición» dos veces— y quien
 * clasifica elige a ciegas entre la que rige y la que no. Eso ya se había
 * corregido en el asistente de radicación y no en el modal de metadatos del
 * expediente: mismo desplegable, misma trampa, arreglada en un sitio de dos.
 * Por eso el criterio vive aquí y no en cada pantalla.
 *
 * NO se filtran las no convalidadas a propósito: una entidad recién
 * provisionada las tiene todas en borrador, y esconderlas la dejaría sin nada
 * que clasificar — un remedio peor que el defecto. Se muestran todas y se dice
 * cuál es cuál.
 *
 * @param {Array<{id: string, code?: string, name?: string, version?: number, estado?: string}>} series
 * @returns {Array<{value: string, label: string}>}
 */
export function opcionesSerieTrd(series) {
  return [...(series ?? [])]
    .sort(
      (a, b) =>
        (ORDEN_ESTADO[a.estado] ?? 9) - (ORDEN_ESTADO[b.estado] ?? 9) ||
        String(a.code ?? "").localeCompare(String(b.code ?? "")) ||
        (a.version ?? 0) - (b.version ?? 0),
    )
    .map((s) => ({
      value: s.id,
      label:
        `${s.code}${s.version ? ` v${s.version}` : ""} — ${s.name}` +
        (ESTADO_TEXTO[s.estado] ? ` (${ESTADO_TEXTO[s.estado]})` : ""),
    }));
}
