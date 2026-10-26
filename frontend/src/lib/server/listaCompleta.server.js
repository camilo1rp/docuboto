// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * listaCompleta.server.js — traer un listado ENTERO del gateway, o decir que no.
 *
 * Por qué existe: ocho loaders pedían `?size=200` con el comentario «para traer
 * el árbol completo de una vez». Doscientos **es el tope del backend**
 * (`le=200`), no una holgura: a partir de la fila 201 la vista se quedaba corta
 * y **nadie se enteraba**, porque el `X-Total-Count` que el servicio ya emite no
 * lo miraba ninguno de los ocho.
 *
 * El daño no es solo «faltan filas». Las vistas de TRD y TVD arman jerarquía por
 * `parent_id`: una subserie cuya serie padre quedó fuera del corte **no aparece
 * en ninguna rama**, así que el árbol no se ve incompleto — se ve distinto. Y en
 * un selector de personas, quien no entró en las primeras 200 sencillamente no
 * existe para quien busca.
 *
 * Qué hace: pide páginas hasta cubrir el `X-Total-Count`, y si aun así no cabe,
 * **lo dice**. Devolver una lista corta con cara de completa es lo que se viene
 * a evitar; un tope sin aviso solo mueve el mismo defecto más lejos.
 */

/** Tope del backend por página (`size: int = Query(..., le=200)`). */
const TAMANO_PAGINA = 200;

/**
 * Número de páginas que se aceptan antes de rendirse. 25 × 200 = 5.000 filas:
 * por encima de eso, una pantalla que pinta la lista entera ya no es la
 * herramienta adecuada y el aviso es más útil que el volcado.
 */
const MAX_PAGINAS = 25;

/**
 * @typedef {object} ListaCompleta
 * @property {any[]} items — lo que se pudo traer.
 * @property {number} total — lo que el servidor dice que hay (`X-Total-Count`).
 * @property {boolean} incompleta — `true` si `items` NO son todos. Quien pinta
 *   la lista debe decirlo en pantalla: es la diferencia entre una vista parcial
 *   y una vista que miente.
 * @property {string|null} error — mensaje si la carga falló del todo.
 */

/**
 * Trae todas las páginas de un listado paginado del gateway.
 *
 * @param {typeof fetch} fetchFn
 * @param {string} base — URL sin `page`/`size` (puede llevar otros filtros).
 * @param {Record<string, string>} headers
 * @param {(cuerpo: any, porDefecto: string) => string} aMensaje — traductor de error.
 * @returns {Promise<ListaCompleta>}
 */
export async function traerListaCompleta(fetchFn, base, headers, aMensaje) {
  const items = [];
  let total = 0;

  for (let pagina = 1; pagina <= MAX_PAGINAS; pagina += 1) {
    const separador = base.includes("?") ? "&" : "?";
    const url = `${base}${separador}page=${pagina}&size=${TAMANO_PAGINA}`;

    let res;
    try {
      res = await fetchFn(url, { headers });
    } catch {
      // Una página que no llega deja la lista incompleta, pero lo ya traído
      // sigue sirviendo: se devuelve marcado, no se descarta.
      return {
        items,
        total,
        incompleta: true,
        error: pagina === 1 ? "No se pudo conectar al servidor." : null,
      };
    }

    if (!res.ok) {
      const cuerpo = await res.json().catch(() => ({}));
      return {
        items,
        total,
        incompleta: true,
        error: pagina === 1 ? aMensaje(cuerpo, `Error ${res.status}`) : null,
      };
    }

    // El total se relee en CADA página, no solo en la primera: entre dos
    // peticiones alguien puede crear una serie, y quedarse con el primer valor
    // daría por completa una lista a la que ya le falta la nueva.
    const cabecera = Number(res.headers.get("X-Total-Count"));
    if (Number.isFinite(cabecera) && cabecera > 0) total = cabecera;

    const lote = await res.json().catch(() => []);
    if (!Array.isArray(lote)) break;
    items.push(...lote);

    // Se para por el TAMAÑO DEL LOTE y no por el total: si el servidor no emite
    // la cabecera —o la emite mal—, un bucle que confíe solo en el total no
    // termina nunca, o termina en la primera página sin haber traído nada.
    if (lote.length < TAMANO_PAGINA) break;
  }

  return {
    items,
    total: Math.max(total, items.length),
    incompleta: items.length < total,
    error: null,
  };
}
