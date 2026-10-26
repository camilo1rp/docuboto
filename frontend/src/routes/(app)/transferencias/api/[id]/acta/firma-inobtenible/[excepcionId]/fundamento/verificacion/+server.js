// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /transferencias/api/[id]/acta/firma-inobtenible/[excepcionId]/fundamento/verificacion —
 * Proxy SSR de la verificación del fundamento de una constancia (D-05, FIRMA-2,
 * ADR-030) — `GET /transferencias/{id}/acta/firma-inobtenible/{excepcion_id}/fundamento/verificacion`.
 *
 * Solo lectura y sin cuerpo. Quién puede pedirla (`USUA_PERM_ADMIN` nivel 3, no-read-up)
 * lo decide el backend; el resultado no incluye el texto del fundamento.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params }) {
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
      `${API_V1}/transferencias/${encodeURIComponent(params.id)}/acta/firma-inobtenible/${encodeURIComponent(params.excepcionId)}/fundamento/verificacion`,
      { headers },
    );
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo verificar el fundamento." },
      { status: 503 },
    );
  }
}
