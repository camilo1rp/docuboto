// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import { getContext, setContext } from "svelte";
import { readable, writable } from "svelte/store";
import { mapaNombres } from "$lib/utils/dependenciaNombre.js";

/**
 * Nombres ACTUALES de las dependencias por código (ADR-032), por CONTEXTO de
 * página y no por store de módulo: un `writable` a nivel de módulo se comparte
 * entre todas las peticiones SSR del proceso y filtraría el catálogo de un
 * tenant a otro. La página que ya cargó el catálogo (la bandeja) crea el
 * contexto; los componentes de flujo lo leen. Sin contexto -> Map vacío (cada
 * componente cae a la instantánea).
 */
export const NOMBRES_DEPENDENCIA_KEY = Symbol("nombresDependencia");

/**
 * Crea el store de nombres de ESTA página y lo publica a sus descendientes.
 * Llamar en la inicialización del componente.
 * @param {Array<{codigo?: number|string, nombre?: string}>|null|undefined} catalogo
 * @returns {import('svelte/store').Writable<Map<number, string>>}
 */
export function crearContextoDependencias(catalogo) {
  const store = writable(mapaNombres(catalogo));
  setContext(NOMBRES_DEPENDENCIA_KEY, store);
  return store;
}

/**
 * Store de nombres del contexto de la página (Map vacío si no hay).
 * Llamar en la inicialización del componente.
 * @returns {import('svelte/store').Readable<Map<number, string>>}
 */
export function usarNombresDependencia() {
  return getContext(NOMBRES_DEPENDENCIA_KEY) ?? readable(new Map());
}
