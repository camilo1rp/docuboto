// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /transferencias/api/[id]/acta/firma-inobtenible/[excepcionId]/revocacion/worm/reintentar — Proxy SSR
 * del reintento manual de la protección WORM de la revocación (D-05, FIRMA-2) —
 * `POST /transferencias/{id}/acta/firma-inobtenible/{excepcion_id}/revocacion/worm/reintentar`.
 *
 * Sin cuerpo. Quién puede (`USUA_PERM_ADMIN` nivel 3, no-read-up `404`) lo decide el backend.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  if (!UUID_RE.test(params.excepcionId ?? "")) {
    return json(
      { error: "validation_error", detail: "La excepción no es válida." },
      { status: 422 },
    );
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${token}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(
      `${API_V1}/transferencias/${encodeURIComponent(params.id)}/acta/firma-inobtenible/${encodeURIComponent(params.excepcionId)}/revocacion/worm/reintentar`,
      { method: "POST", headers },
    );
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo reintentar la protección." },
      { status: 503 },
    );
  }
}
