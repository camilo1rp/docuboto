// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * (public)/eliminacion/[id] — El inventario publicado de un lote.
 *
 * El backend ya retiene la DESCRIPCIÓN de los expedientes clasificados y marca
 * la fila con `nombre_reservado`; aquí no se filtra nada — se PINTA lo que
 * llega, y el hueco se explica en vez de dejarse mudo.
 *
 * Un lote en borrador o anulado responde 404 y aquí se convierte en 404: no se
 * distingue de un identificador inventado.
 */
import { error } from "@sveltejs/kit";

import { API_V1, PUBLIC_TENANT } from "$lib/utils/config.server.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ params, fetch }) {
  if (!PUBLIC_TENANT) {
    throw error(404, { message: "Publicidad de eliminación no disponible" });
  }
  const res = await fetch(
    `${API_V1}/public/archive/eliminacion/${encodeURIComponent(PUBLIC_TENANT)}/lotes/${encodeURIComponent(params.id)}`,
  );
  if (res.status === 404) {
    throw error(404, { message: "No hay ningún inventario publicado con ese identificador" });
  }
  if (!res.ok) {
    throw error(502, { message: "No se pudo consultar el inventario publicado" });
  }
  return { lote: await res.json() };
}
