// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /bandeja/api/pending — Proxy SSR: pasos pendientes del equipo (D-05, ADR-011,
 * E05 §Fase 1).
 *
 * El token se lee de la cookie httpOnly del servidor y nunca se expone al JS del
 * cliente. El backend re-valida tenant y resuelve el clearance real del llamante
 * (RF-SEG-08, no-read-up) — X-User-Id es opcional aquí, igual que en /inbox.
 *
 * GET → lista de pasos pendientes (`GET /workflows/pending`)
 *   Parámetros: dept_code (int), assigned_to (uuid), page, size
 *   Respuesta: { items: FlowStepResponse[], total: number }
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { parseCodigoDependencia } from "$lib/utils/dependenciaNombre.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  const assignedTo = url.searchParams.get("assigned_to") ?? "";
  if (assignedTo && !UUID_RE.test(assignedTo)) {
    return json(
      { error: "validation_error", detail: "assigned_to debe ser un UUID válido." },
      { status: 400 },
    );
  }

  // ADR-032: la dependencia se filtra por CÓDIGO (entero); texto libre se descarta.
  const deptCode = parseCodigoDependencia(url.searchParams.get("dept_code"));
  const page = url.searchParams.get("page") ?? "1";
  const size = url.searchParams.get("size") ?? "20";

  // Allowlist de query params: solo los que el backend acepta.
  const params = new URLSearchParams({ page, size });
  if (deptCode !== null) params.set("dept_code", String(deptCode));
  if (assignedTo) params.set("assigned_to", assignedTo);

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/workflows/pending?${params}`, { headers });

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      return json(data, { status: res.status });
    }

    const total = Number(res.headers.get("X-Total-Count") ?? 0);
    const items = await res.json().catch(() => []);
    return json({ items, total });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de flujos." },
      { status: 503 },
    );
  }
}
