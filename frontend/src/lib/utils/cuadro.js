// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * cuadro.js — presentación del cuadro de clasificación derivado (C-4, ADR-040).
 *
 * La clave de un nodo es su CAMINO (códigos de la raíz al nodo), no su
 * `codigo`: tras una reorganización un mismo código de dependencia puede
 * aparecer bajo dos padres, y una clave por `codigo` repetiría la fila.
 */

/** @type {Record<string,string>} */
const MOTIVOS = {
  sin_productora: "Sin dependencia productora declarada",
  productora_desconocida: "La dependencia productora ya no existe en el organigrama",
  serie_padre_no_vigente: "Su serie padre no está vigente",
};

/** @param {string} motivo */
export function motivoSinSeccion(motivo) {
  return MOTIVOS[motivo] ?? motivo;
}

/**
 * Texto en español de un aviso del cuadro.
 * @param {{ tipo: string, dependencia_codigo?: number|null, series?: string[]|null }} aviso
 */
export function textoAviso(aviso) {
  const dep = aviso.dependencia_codigo != null ? ` ${aviso.dependencia_codigo}` : "";
  const series = aviso.series?.length ? ` Series: ${aviso.series.join(", ")}.` : "";
  switch (aviso.tipo) {
    case "productora_inactiva":
      return `La dependencia productora${dep} está inactiva; sus series siguen en el cuadro.${series}`;
    case "organigrama_reorganizado_tras_convalidacion":
      return `El organigrama cambió después de convalidar las series de la dependencia${dep}: su ubicación actual difiere de la que aprobó el Consejo.${series}`;
    case "productora_desconocida":
      return `La dependencia productora${dep} no existe en el organigrama; sus series aparecen en «Sin ubicar».${series}`;
    case "entidad_sin_nombre":
      return "La entidad no tiene nombre configurado, así que el fondo se muestra sin nombre. Configúrelo en Parámetros.";
    default:
      return `Aviso del cuadro: ${aviso.tipo}${dep}.${series}`;
  }
}

/**
 * @typedef {object} FilaCuadro
 * @property {string} key camino único (códigos de la raíz al nodo)
 * @property {number} depth
 * @property {"seccion"|"subseccion"|"serie"|"subserie"} kind
 * @property {string} etiqueta
 * @property {any} dato
 */

/**
 * Aplana secciones > subsecciones > series > subseries en filas con camino.
 * @param {any[]} secciones
 * @returns {FilaCuadro[]}
 */
export function aplanarCuadro(secciones) {
  /** @type {FilaCuadro[]} */
  const filas = [];
  /** @param {any} nodo @param {string} prefijo @param {number} depth */
  const visitar = (nodo, prefijo, depth) => {
    const camino = prefijo ? `${prefijo}/${nodo.codigo}` : String(nodo.codigo);
    filas.push({
      key: camino,
      depth,
      kind: nodo.nivel === "subseccion" ? "subseccion" : "seccion",
      etiqueta: nodo.nombre,
      dato: nodo,
    });
    for (const serie of nodo.series ?? []) {
      const k = `${camino}#${serie.code}`;
      filas.push({ key: k, depth: depth + 1, kind: "serie", etiqueta: serie.name, dato: serie });
      for (const sub of serie.subseries ?? []) {
        filas.push({
          key: `${k}#${sub.code}`,
          depth: depth + 2,
          kind: "subserie",
          etiqueta: sub.name,
          dato: sub,
        });
      }
    }
    for (const hijo of nodo.subsecciones ?? []) visitar(hijo, camino, depth + 1);
  };
  for (const s of secciones ?? []) visitar(s, "", 0);
  return filas;
}
