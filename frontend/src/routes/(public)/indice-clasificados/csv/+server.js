// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /indice-clasificados/csv — Descarga pública del índice en CSV (Ley 1712 art. 20).
 *
 * GET → GET /api/v1/public/archive/indice-clasificados/{tenant}.csv
 *
 * Sin sesión a propósito. Pasa por el servidor para que la URL interna del gateway
 * y el tenant no viajen al navegador. El CSV ya neutraliza la inyección de fórmulas
 * en el servidor (`_csv_safe`). Un fallo del origen NO se convierte en un CSV vacío:
 * un archivo sin filas afirmaría que no hay nada clasificado.
 */

import { json } from "@sveltejs/kit";
import { API_V1, PUBLIC_TENANT } from "$lib/utils/config.server.js";

/** @type {import('./$types').RequestHandler} */
export async function GET({ fetch }) {
  if (!PUBLIC_TENANT) {
    return json(
      { error: "indice_no_habilitado", detail: "Esta entidad no tiene portal público disponible." },
      { status: 503 },
    );
  }
  try {
    const res = await fetch(
      `${API_V1}/public/archive/indice-clasificados/${encodeURIComponent(PUBLIC_TENANT)}.csv`,
    );
    if (!res.ok) {
      return json(
        {
          error: "indice_no_disponible",
          detail: "El índice no se puede componer en este momento. Intente de nuevo más tarde.",
        },
        { status: res.status === 404 ? 404 : 503 },
      );
    }
    return new Response(await res.text(), {
      status: 200,
      headers: {
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": "attachment; filename=indice-clasificados.csv",
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar con el servidor de archivo." },
      { status: 503 },
    );
  }
}
