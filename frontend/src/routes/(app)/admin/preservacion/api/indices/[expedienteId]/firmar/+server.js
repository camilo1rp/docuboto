// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/preservacion/api/indices/[expedienteId]/firmar — Proxy SSR del
 * reintento MANUAL del sellado XAdES-B del índice electrónico de un
 * expediente (D-05, E06 Inc.1, RT-16) —
 * `POST /expedientes/{expediente_id}/indice/firmar` (archive-service),
 * gateado `USUA_PERM_EXPEDIENTE` + `PERM_FIRMA`.
 *
 * Se dispara desde la fila correspondiente en el listado de
 * "índices pendientes de firma" — idempotente en el backend (409 si el
 * índice vigente ya está firmado).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

export async function POST({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  const { expedienteId } = params;

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(
      `${API_V1}/expedientes/${encodeURIComponent(expedienteId)}/indice/firmar`,
      { method: "POST", headers },
    );
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo reintentar el sellado del índice." },
      { status: 503 },
    );
  }
}
