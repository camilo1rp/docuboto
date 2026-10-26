// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
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

/**
 * Estados bajo los que un instrumento RIGE. El resto no gobierna nada: una
 * versión derogada fue reemplazada, y una en borrador o aprobada todavía no ha
 * sido convalidada por la instancia competente (Ac. AGN 001/2024, ADR-025).
 */
const ESTADOS_VIGENTES = new Set(["convalidada", "migrada"]);

/**
 * Qué decirle a quien mira una serie TRD ya asignada: bajo qué versión está y
 * si esa versión sigue rigiendo.
 *
 * **Por qué hace falta.** La ficha del expediente pintaba «DEM-100 — Derechos
 * de Petición» a secas. `EXP-2026-0001` llevaba días apuntando a la **v1
 * derogada** —el circuito de convalidación había abierto y convalidado una v2—
 * y la pantalla no lo decía: quien clasificaba, foliaba o decidía cerrar creía
 * estar trabajando bajo el instrumento vigente.
 *
 * El cierre re-resuelve la serie por su CÓDIGO y salta a la versión vigente,
 * así que el expediente no queda gobernado por lo derogado (verificado: el
 * asiento del cierre lleva `trd_serie_id_previo` y `trd_serie_id` distintos).
 * Pero eso ocurre al final y sin avisar. Entre tanto, la persona toma
 * decisiones creyendo otra cosa, y el sistema tiene el dato para decírselo.
 *
 * **Tercera vez que se arregla este mismo descuido**: `opcionesSerieTrd` (aquí
 * al lado) nació porque el desplegable mostraba dos opciones idénticas, y su
 * comentario ya dejaba escrito que la corrección se había hecho en el asistente
 * de radicación y no en el modal de metadatos — «mismo desplegable, misma
 * trampa, arreglada en un sitio de dos». La ficha era el tercer sitio. Por eso
 * esto vive aquí, con `ESTADO_TEXTO`, y no resuelto suelto en cada pantalla.
 *
 * No se oculta ni se bloquea nada: la decisión es del archivista. Lo único que
 * cambia es que la toma sabiendo bajo qué instrumento está.
 *
 * @param {{code?: string, name?: string, version?: number, estado?: string}|null|undefined} serie
 * @returns {{etiqueta: string|null, version: number|null, estado: string|null,
 *   estadoTexto: string|null, rige: boolean}}
 */
export function vigenciaSerieTrd(serie) {
  const base = nombreSerieTrd(serie);
  const version = typeof serie?.version === "number" ? serie.version : null;
  const estado = typeof serie?.estado === "string" ? serie.estado : null;
  // La versión va pegada al código —«DEM-100 v1 — Derechos de Petición»—
  // igual que en el desplegable, para que las dos pantallas se lean igual.
  const etiqueta =
    base && version && serie?.code ? base.replace(serie.code, `${serie.code} v${version}`) : base;
  return {
    etiqueta,
    version,
    estado,
    estadoTexto: estado ? (ESTADO_TEXTO[estado] ?? estado) : null,
    // Sin estado NO se afirma que rige: callar es mejor que asegurar de más
    // sobre el instrumento que gobierna la retención de un expediente.
    rige: estado ? ESTADOS_VIGENTES.has(estado) : false,
  };
}
