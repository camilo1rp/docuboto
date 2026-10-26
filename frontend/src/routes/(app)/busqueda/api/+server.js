// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /busqueda/api — Endpoint proxy de servidor para búsqueda avanzada (D-05, ADR-011, T-11).
 *
 * El navegador llama a este endpoint local (sin token); el servidor lee el token de
 * la cookie httpOnly y lo propaga al gateway — el access_token nunca alcanza el
 * JS del cliente.
 *
 * Inyecta X-User-Id (desde locals.user.sub) requerido por el endpoint de búsqueda
 * para aplicar clearance por nivel (no read-up, RF-SEG-08).
 *
 * GET → resultados de búsqueda paginados
 *   Parámetros: q (≥2), doc_type, status, date_from, date_to, dest_dept_code, ubicacion_dept_code, page, size
 *   Respuesta: { results: SearchItem[], total: number }
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
  const userId = locals.user?.sub ?? "";

  const q = url.searchParams.get("q") ?? "";
  const docType = url.searchParams.get("doc_type") ?? "";
  const status = url.searchParams.get("status") ?? "";
  const dateFrom = url.searchParams.get("date_from") ?? "";
  const dateTo = url.searchParams.get("date_to") ?? "";
  // dest_dept_code debe ser un entero positivo; descartar texto libre que produciría 422
  const rawDeptCode = url.searchParams.get("dest_dept_code") ?? "";
  const destDeptCodeNum =
    rawDeptCode !== "" && /^\d+$/.test(rawDeptCode.trim()) ? Math.floor(Number(rawDeptCode)) : null;
  // ubicacion_dept_code (ADR-029): mismo saneo (entero positivo)
  const rawUbicCode = url.searchParams.get("ubicacion_dept_code") ?? "";
  const ubicacionCodeNum =
    rawUbicCode !== "" && /^\d+$/.test(rawUbicCode.trim()) ? Math.floor(Number(rawUbicCode)) : null;
  const page = url.searchParams.get("page") ?? "1";
  // Acotar size: 1-100 para evitar peticiones abusivas al gateway
  const rawSize = url.searchParams.get("size") ?? "20";
  const size = String(Math.min(100, Math.max(1, Number(rawSize) || 20)));

  if (q.length < 2) {
    return json(
      { error: "validation_error", detail: "La búsqueda requiere al menos 2 caracteres." },
      { status: 400 },
    );
  }

  const params = new URLSearchParams({ q, page, size });
  if (docType) params.set("doc_type", docType);
  if (status) params.set("status", status);
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);
  if (destDeptCodeNum !== null) params.set("dest_dept_code", String(destDeptCodeNum));
  if (ubicacionCodeNum !== null) params.set("ubicacion_dept_code", String(ubicacionCodeNum));

  try {
    const res = await fetch(`${API_V1}/search?${params.toString()}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
        "X-User-Id": userId,
      },
    });

    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      return json(data, { status: res.status });
    }

    // SearchResponse devuelve { query, total, hits, page, size }; `hits` es el nombre
    // real del campo en app/schemas/search.py. Se mantienen `items`/`results` como
    // respaldo por si otro endpoint reutiliza este normalizador.
    let results, total;
    if (Array.isArray(data)) {
      results = data;
      total = Number(res.headers.get("X-Total-Count") ?? data.length);
    } else {
      results = data.hits ?? data.items ?? data.results ?? [];
      total = data.total ?? Number(res.headers.get("X-Total-Count") ?? 0);
    }

    return json({ results, total });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de búsqueda." },
      { status: 503 },
    );
  }
}
