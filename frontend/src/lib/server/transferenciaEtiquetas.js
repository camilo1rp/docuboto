// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Anota cada transferencia con el CÓDIGO de su expediente.
 *
 * El listado de transferencias identifica el expediente por su UUID, un
 * identificador que no está impreso en ninguna carátula ni en ninguna
 * signatura topográfica: a quien mira la lista no le dice nada. El detalle y
 * el acta ya lo resolvían; el listado no.
 *
 * Vive en el servidor y no en el `load` de la página **a propósito**. La lista
 * se recarga por el cliente al crear una transferencia y al aplicar filtros,
 * y un mapa construido solo en el `load` deja sin etiqueta todo lo que no
 * viniera en la carga inicial — el defecto reaparecía en cuanto se filtraba.
 * Anotando en el proxy, la etiqueta viaja con la fila por los dos caminos.
 *
 * NO-READ-UP: `obtenerExpediente` devuelve `null` para un expediente que quien
 * mira no puede leer, y entonces la fila se queda sin `expediente_codigo`. La
 * etiqueta jamás puede revelar lo que el control de acceso oculta.
 */

/** Tope defensivo: una página no debe disparar cientos de peticiones al gateway. */
export const MAX_EXPEDIENTES_RESUELTOS = 60;

/**
 * @param {any} listado - respuesta del gateway: `{items: […]}` o lista pelada.
 * @param {(id: string) => Promise<any|null>} obtenerExpediente
 * @returns {Promise<any>} el mismo listado con `expediente_codigo`/`expediente_nombre`
 *   añadidos a cada fila que se pudo resolver. Conserva la forma de entrada.
 */
export async function anotarCodigoExpediente(listado, obtenerExpediente) {
  const filas = Array.isArray(listado?.items)
    ? listado.items
    : Array.isArray(listado)
      ? listado
      : null;
  if (!filas || filas.length === 0) return listado;

  const ids = [...new Set(filas.map((t) => t?.expediente_id).filter(Boolean))].slice(
    0,
    MAX_EXPEDIENTES_RESUELTOS,
  );

  const pares = await Promise.all(
    ids.map(async (id) => {
      try {
        const exp = await obtenerExpediente(id);
        return exp ? [id, exp] : null;
      } catch {
        // Una faceta de presentación no puede tumbar el listado: sin ella se
        // muestra el identificador, que es lo que había antes.
        return null;
      }
    }),
  );
  /** @type {Map<string, any>} */
  const porId = new Map(/** @type {Array<[string, any]>} */ (pares.filter(Boolean)));
  if (porId.size === 0) return listado;

  const anotadas = filas.map((t) => {
    const exp = t?.expediente_id ? porId.get(t.expediente_id) : null;
    if (!exp) return t;
    return {
      ...t,
      expediente_codigo: exp.code ?? null,
      expediente_nombre: exp.name ?? null,
    };
  });

  return Array.isArray(listado) ? anotadas : { ...listado, items: anotadas };
}
