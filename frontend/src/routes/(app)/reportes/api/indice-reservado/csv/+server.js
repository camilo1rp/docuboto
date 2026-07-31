// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /reportes/api/indice-reservado/csv — Proxy de descarga CSV del índice de
 * información clasificada y reservada (E08, Ley 1712/2014 art. 20, D-05).
 *
 * Endpoint real: GET /api/v1/reports/indice-reservado.csv
 * (services/document-service/app/routers/reports.py:154), gateado por
 * `PERM_RECLASIFICAR`. El backend YA neutraliza la inyección de fórmulas CSV
 * en las columnas de texto libre (`_csv_safe`, prefijo `'` en celdas que
 * empiezan por un signo igual/más/menos/arroba o un control TAB/CR) — este
 * proxy hace pass-through del blob sin reprocesarlo (mismo patrón que
 * /reportes/api/csv).
 *
 * Exporta el registro COMPLETO (todos los radicados nivel ≥ 2), no solo la
 * página visible en pantalla — a diferencia de la exportación local del
 * DataTable (que solo exporta las filas cargadas en el cliente).
 *
 * GET → streams CSV de /api/v1/reports/indice-reservado.csv
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  let res;
  try {
    res = await fetch(`${API_V1}/reports/indice-reservado.csv`, { headers });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo descargar el índice en CSV." },
      { status: 503 },
    );
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({
      error: "upstream_error",
      detail: `HTTP ${res.status}`,
    }));
    return json(body, { status: res.status });
  }

  const blob = await res.blob();
  const contentDisposition =
    res.headers.get("Content-Disposition") ?? "attachment; filename=indice-reservado.csv";

  return new Response(blob, {
    status: 200,
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": contentDisposition,
    },
  });
}
