// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * (public)/eliminacion — Publicidad previa de la eliminación documental.
 *
 * RF-TRD-07, fase 2. El inventario de lo que la entidad va a eliminar se pone a
 * la vista de cualquiera, **sin sesión**, para que quien tenga algo que oponer
 * pueda hacerlo ANTES de que se destruya.
 *
 * Sin `PORTAL_TENANT_SLUG` configurado la página se declara no disponible en vez
 * de consultar con un tenant vacío: es el mismo criterio que la consulta
 * ciudadana, y evita la clase de fallo mudo que ya costó una tarde
 * (`PUBLIC_` era prefijo reservado y la variable llegaba `undefined`).
 */
import { API_V1, PUBLIC_TENANT } from "$lib/utils/config.server.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ fetch }) {
  if (!PUBLIC_TENANT) {
    return { disponible: false, lotes: [], loadError: null };
  }
  try {
    const res = await fetch(
      `${API_V1}/public/archive/eliminacion/${encodeURIComponent(PUBLIC_TENANT)}/lotes`,
    );
    if (res.status === 404) {
      // Entidad sin portal público (inexistente, dada de baja o sin aprovisionar):
      // no se distingue cuál (ADR-035).
      return { disponible: false, lotes: [], loadError: null };
    }
    if (!res.ok) {
      return {
        disponible: true,
        lotes: [],
        loadError: "No se pudo consultar los inventarios publicados en este momento.",
      };
    }
    return { disponible: true, lotes: await res.json().catch(() => []), loadError: null };
  } catch {
    return {
      disponible: true,
      lotes: [],
      loadError: "No se pudo conectar con el servidor de archivo.",
    };
  }
}
