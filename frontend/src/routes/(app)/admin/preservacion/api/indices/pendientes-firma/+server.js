// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/preservacion/api/indices/pendientes-firma — Proxy SSR de
 * visibilidad operacional de índices electrónicos pendientes de sellado
 * XAdES (D-05, Ac. AGN 001/2024 art. 4.3.2.4) —
 * `GET /expedientes/indices/pendientes-firma` (archive-service).
 *
 * No-read-up (RF-SEG-08): el backend ya filtra por el clearance real del
 * llamante — este proxy solo reenvía paginación y expone `X-Total-Count`.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

export async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  const page = Math.max(1, Number(url.searchParams.get("page")) || 1);
  const sizeRaw = Number(url.searchParams.get("size")) || 20;
  const size = Math.min(100, Math.max(1, sizeRaw));

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(
      `${API_V1}/expedientes/indices/pendientes-firma?page=${page}&size=${size}`,
      { headers },
    );
    const data = await res.json().catch(() => ({}));
    const totalCount = res.headers.get("X-Total-Count");
    return json(data, {
      status: res.status,
      headers: totalCount ? { "X-Total-Count": totalCount } : undefined,
    });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo obtener los índices pendientes de firma." },
      { status: 503 },
    );
  }
}
