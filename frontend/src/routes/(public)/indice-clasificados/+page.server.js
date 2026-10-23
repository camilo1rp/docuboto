// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * (public)/indice-clasificados — Índice público de información clasificada y
 * reservada, por serie documental (Ley 1712 de 2014, art. 20; ADR-039 / C12).
 *
 * Es una OBLIGACIÓN LEGAL de publicidad: se sirve sin sesión y no depende de que
 * la entidad haya habilitado la interoperabilidad pública. El contenido es
 * content-free por construcción (sin números de radicado, asuntos ni
 * fundamentos): lo garantiza archive-service, aquí solo se dibuja.
 *
 * Sin `PORTAL_TENANT_SLUG` la página se declara no disponible en vez de consultar
 * con un tenant vacío (mismo criterio que /eliminacion y /consulta). Un 503 NO se
 * presenta como lista vacía: «el índice no se pudo componer» y «no hay nada
 * clasificado» son afirmaciones distintas, y la segunda sería falsa.
 */
import { API_V1, PUBLIC_TENANT } from "$lib/utils/config.server.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ fetch }) {
  if (!PUBLIC_TENANT) {
    return { disponible: false, filas: [], loadError: null };
  }
  try {
    const res = await fetch(
      `${API_V1}/public/archive/indice-clasificados/${encodeURIComponent(PUBLIC_TENANT)}`,
    );
    if (res.status === 404) {
      return { disponible: false, filas: [], loadError: null };
    }
    if (!res.ok) {
      return {
        disponible: true,
        filas: [],
        loadError:
          res.status === 503
            ? "El índice no se puede componer en este momento. Intente de nuevo en unos minutos."
            : "No se pudo consultar el índice en este momento.",
      };
    }
    const cuerpo = await res.json().catch(() => []);
    return { disponible: true, filas: Array.isArray(cuerpo) ? cuerpo : [], loadError: null };
  } catch {
    return {
      disponible: true,
      filas: [],
      loadError: "No se pudo conectar con el servidor de archivo.",
    };
  }
}
