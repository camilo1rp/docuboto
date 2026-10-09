// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Carga SSR de la ventanilla en línea: qué trámites recibe esta entidad.
 *
 * Se resuelve en el servidor y no en el cliente para que el formulario llegue
 * ya utilizable: si la lista se pidiera al montar, quien entra vería un
 * desplegable vacío durante un instante y no sabría si es que la entidad no
 * recibe nada o si aún está cargando.
 */

import { API_V1, POLITICA_URL, POLITICA_VERSION, PUBLIC_TENANT } from "$lib/utils/config.server.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ fetch }) {
  // La política viaja en TODA respuesta: el formulario no se puede enviar sin ella.
  const politica = { version: POLITICA_VERSION, url: POLITICA_URL };
  if (!PUBLIC_TENANT) {
    return { tipos: [], habilitada: false, politica };
  }
  try {
    const res = await fetch(
      `${API_V1}/public/${encodeURIComponent(PUBLIC_TENANT)}/ventanilla/tipos`,
    );
    const tipos = res.ok ? await res.json() : [];
    // Sin tipos declarados la ventanilla NO está operativa: el backend
    // rechazaría cualquier radicación con 422. Decirlo aquí evita que alguien
    // rellene un formulario entero para chocar contra un error al final.
    return { tipos, habilitada: Array.isArray(tipos) && tipos.length > 0, politica };
  } catch {
    return { tipos: [], habilitada: false, politica };
  }
}
