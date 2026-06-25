// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * +layout.js — Layout raíz universal (SSR + cliente).
 *
 * Activa SSR en todas las rutas (coherente con adapter-node, ADR-011).
 * El layout SSR real de las rutas privadas está en (app)/+layout.server.js.
 */

/** @type {import('./$types').LayoutLoad} */
export async function load() {
  return {};
}

export const ssr = true;
export const prerender = false;
