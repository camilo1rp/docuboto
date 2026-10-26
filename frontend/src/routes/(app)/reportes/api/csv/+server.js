// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /reportes/api/csv — Proxy de descarga CSV de radicados (D-05, ADR-011, T-11).
 *
 * El navegador llama a este endpoint local; el servidor lee el token de la cookie
 * httpOnly y lo propaga al gateway — el access_token nunca alcanza el cliente.
 *
 * Hace pass-through del blob CSV preservando Content-Type y Content-Disposition
 * del gateway (patrón análogo a expedientes/[id]/api/indice-xml/+server.js).
 *
 * GET → streams CSV de /api/v1/reports/radicados.csv
 *   Parámetros: date_from, date_to (opcionales)
 *   Respuesta: text/csv con Content-Disposition: attachment; filename=radicados.csv
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  // X-User-Id: inyectar para que quede activo cuando el backend de reportes aplique clearance
  const userId = locals.user?.sub ?? "";

  // Saneamiento de parámetros: solo fechas con formato YYYY-MM-DD
  const dateFrom = url.searchParams.get("date_from") ?? "";
  const dateTo = url.searchParams.get("date_to") ?? "";

  const params = new URLSearchParams();
  if (dateFrom && /^\d{4}-\d{2}-\d{2}$/.test(dateFrom)) params.set("date_from", dateFrom);
  if (dateTo && /^\d{4}-\d{2}-\d{2}$/.test(dateTo)) params.set("date_to", dateTo);
  const qs = params.toString();

  let res;
  try {
    res = await fetch(`${API_V1}/reports/radicados.csv${qs ? `?${qs}` : ""}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
        "X-User-Id": userId,
      },
    });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo descargar el reporte CSV." },
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

  // Pass-through del blob CSV preservando los headers de descarga del gateway
  const blob = await res.blob();
  const contentDisposition =
    res.headers.get("Content-Disposition") ?? "attachment; filename=radicados.csv";

  return new Response(blob, {
    status: 200,
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": contentDisposition,
    },
  });
}
