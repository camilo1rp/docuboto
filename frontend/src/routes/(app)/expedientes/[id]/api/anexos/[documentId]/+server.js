// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /expedientes/[id]/api/anexos/[documentId] — Listado de anexos de UN
 * radicado del expediente, mediado por document-service (D-05, E07).
 *
 * CONTRATO: `expediente.radicados[]` (archive-service) NO trae los anexos
 * embebidos por radicado — solo tracking_number/added_at/radicado_id. Los
 * anexos viven en document-service y se obtienen bajo demanda por radicado:
 *
 *   GET /api/v1/documents/{document_id}/anexos
 *     → 200 [{id, file_id, filename, file_size, mime_type, created_at,
 *             tipo_anexo, es_principal, folios}, ...] (puede ser [] — el
 *       radicado es visible y simplemente no tiene anexos)
 *     → 404 NEUTRO (no-read-up, RF-SEG-08): radicado inexistente O por
 *       encima del clearance del llamante — misma respuesta para ambos
 *       casos, este proxy no reconstruye la causal.
 *
 * `documentId` es el `radicado_id` dueño de los anexos (viene de
 * expediente.radicados[].radicado_id) — el mismo id que ya usa el proxy de
 * descarga en ./api/download/[documentId]/[fileId]. `[id]` de esta ruta
 * (el expediente) solo da contexto de breadcrumb; el backend no lo usa.
 *
 * El token se lee de event.locals (cookie httpOnly); el JS del cliente
 * nunca lo ve. X-User-Id se inyecta desde locals.user.sub (nunca del
 * cliente) para que document-service resuelva el clearance real del
 * actor — sin él, resolve_caller_clearance degrada a PUBLICA (fail-closed)
 * y los anexos de radicados reservados/clasificados dejarían de listarse
 * para actores autorizados.
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
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  // Allowlist: documentId (radicado dueño de los anexos) debe ser UUID.
  const { documentId } = params;
  if (!documentId || !UUID_RE.test(documentId)) {
    return json(
      { error: "validation_error", detail: "El identificador del radicado no es válido." },
      { status: 400 },
    );
  }

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  let res;
  try {
    res = await fetch(`${API_V1}/documents/${documentId}/anexos`, { headers });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudieron cargar los anexos." },
      { status: 503 },
    );
  }

  if (!res.ok) {
    // No-read-up: el backend responde el MISMO 404 neutro para "no existe" y
    // "sobre-clearance" — este proxy no debe reconstruir ni filtrar esa
    // distinción a partir de otras señales.
    if (res.status === 404) {
      return json(
        { error: "not_found", detail: "Los anexos de este radicado no están disponibles." },
        { status: 404 },
      );
    }
    const body = await res.json().catch(() => ({
      error: "upstream_error",
      detail: `HTTP ${res.status}`,
    }));
    return json(body, { status: res.status });
  }

  const anexos = await res.json().catch(() => []);
  return json(anexos, { status: 200 });
}
