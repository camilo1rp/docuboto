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

import { API_V1, PUBLIC_TENANT } from "$lib/utils/config.server.js";
import { codigoError } from "$lib/utils/apiError.js";
import { cargarPolitica, SIN_POLITICA } from "$lib/server/politicaVentanilla.server.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ fetch }) {
  if (!PUBLIC_TENANT) {
    return {
      tipos: [],
      habilitada: false,
      sinVentanilla: true,
      politica: { ...SIN_POLITICA, verificable: true },
    };
  }
  const politica = await cargarPolitica(fetch);
  if (politica.canalSuspendido) {
    return { tipos: [], habilitada: false, canalSuspendido: true, politica };
  }
  try {
    const res = await fetch(
      `${API_V1}/public/${encodeURIComponent(PUBLIC_TENANT)}/ventanilla/tipos`,
    );
    // 503 `canal_no_disponible`: entidad suspendida; recepción cerrada, consulta abierta.
    if (
      res.status === 503 &&
      codigoError(await res.json().catch(() => null)) === "canal_no_disponible"
    ) {
      return { tipos: [], habilitada: false, canalSuspendido: true, politica };
    }
    // 404 `not_found` (ADR-035): entidad inexistente, dada de baja, sin aprovisionar
    // o con slug mal formado, sin distinguirlas. Se declara aparte para decir
    // «no hay ventanilla» y no «la entidad aún no la habilitó».
    if (res.status === 404) {
      return { tipos: [], habilitada: false, sinVentanilla: true, politica };
    }
    const tipos = res.ok ? await res.json() : [];
    // Sin tipos declarados la ventanilla NO está operativa: el backend
    // rechazaría cualquier radicación con 422. Decirlo aquí evita que alguien
    // rellene un formulario entero para chocar contra un error al final.
    return { tipos, habilitada: Array.isArray(tipos) && tipos.length > 0, politica };
  } catch {
    return { tipos: [], habilitada: false, politica };
  }
}
