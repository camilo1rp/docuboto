// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import { urlHttpSegura } from "$lib/utils/urlSegura.js";
import { API_V1, PUBLIC_TENANT } from "$lib/utils/config.server.js";
import { codigoError } from "$lib/utils/apiError.js";

/** Política sin publicar o no verificable: el formulario no deja radicar (fail-closed). */
export const SIN_POLITICA = { publicada: false, version: "", url: "", texto: "", huella: "" };

/**
 * Política de tratamiento de datos que publica LA ENTIDAD (ADR-031). Se pide al
 * servidor en cada carga: nada de configuración del frontend que pueda divergir.
 * @param {typeof fetch} fetch
 */
export async function cargarPolitica(fetch) {
  try {
    const res = await fetch(
      `${API_V1}/public/${encodeURIComponent(PUBLIC_TENANT)}/ventanilla/politica`,
    );
    if (
      res.status === 503 &&
      codigoError(await res.json().catch(() => null)) === "canal_no_disponible"
    ) {
      // Entidad suspendida (ADR-035): la recepción está cerrada, no es un fallo.
      return { ...SIN_POLITICA, verificable: false, canalSuspendido: true };
    }
    if (!res.ok) return { ...SIN_POLITICA, verificable: false };
    const p = await res.json();
    return {
      publicada: Boolean(p?.publicada),
      version: p?.version ?? "",
      url: urlHttpSegura(p?.url),
      texto: p?.texto ?? "",
      huella: p?.huella ?? "",
      verificable: true,
      canalSuspendido: false,
    };
  } catch {
    return { ...SIN_POLITICA, verificable: false };
  }
}
