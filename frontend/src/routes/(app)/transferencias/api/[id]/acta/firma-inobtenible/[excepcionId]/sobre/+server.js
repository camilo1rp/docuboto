// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /transferencias/api/[id]/acta/firma-inobtenible/[excepcionId]/sobre — Proxy SSR de la
 * descarga del sobre firmado de una constancia o de su revocación (D-05, FIRMA-2) —
 * `GET /transferencias/{id}/acta/firma-inobtenible/{excepcion_id}/sobre?acto=constancia|revocacion`.
 *
 * Solo `acto` viaja como consulta (allowlist). `USUA_PERM_ADMIN` nivel 3 y el asiento de
 * lectura los decide el backend. El cliente descarga `xml_firmado`; no se pinta.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const ACTOS = ["constancia", "revocacion"];

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params, url }) {
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
  const acto = url.searchParams.get("acto") ?? "constancia";
  if (!ACTOS.includes(acto)) {
    return json({ error: "validation_error", detail: "El acto no es válido." }, { status: 422 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${token}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(
      `${API_V1}/transferencias/${encodeURIComponent(params.id)}/acta/firma-inobtenible/${encodeURIComponent(params.excepcionId)}/sobre?acto=${acto}`,
      { headers },
    );
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo obtener el sobre firmado." },
      { status: 503 },
    );
  }
}
