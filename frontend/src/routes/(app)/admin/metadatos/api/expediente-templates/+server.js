// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/metadatos/api/expediente-templates — Proxy SSR: plantillas de
 * metadatos de EXPEDIENTE (D-05, E03/ADR-007) —
 * services/archive-service/app/routers/metadata.py.
 *
 * GET  ?trd_serie_id= → ExpedienteTemplateResponse[].
 * POST                 → crea una plantilla. Body: { trd_serie_id, json_schema,
 *   version?, activo? }. USUA_PERM_TRD en el backend.
 *
 * Sin PATCH/DELETE en el backend: una plantilla es inmutable una vez creada —
 * "editar" es crear una nueva versión (`version` incremental) para la misma
 * serie. `json_schema` se valida como JSON antes de reenviarlo: un objeto
 * roto viajaría como 422 opaco del backend en vez de un error legible aquí.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function headersFor(locals, withContentType = false) {
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${locals.accessToken}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;
  if (withContentType) headers["Content-Type"] = "application/json";
  return headers;
}

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, url }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const params = new URLSearchParams();
  const serieId = url.searchParams.get("trd_serie_id");
  if (serieId && UUID_RE.test(serieId)) params.set("trd_serie_id", serieId);

  try {
    const res = await fetch(`${API_V1}/expediente-metadata/templates?${params}`, {
      headers: headersFor(locals),
    });
    const data = await res.json().catch(() => []);
    return json(data, { status: res.status });
  } catch {
    return json(
      {
        error: "connection_error",
        detail: "No se pudo conectar al servidor de plantillas de expediente.",
      },
      { status: 503 },
    );
  }
}

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, request }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const { trd_serie_id, json_schema, version, activo } = body ?? {};

  if (typeof trd_serie_id !== "string" || !UUID_RE.test(trd_serie_id)) {
    return json(
      { error: "validation_error", detail: "Serie TRD inválida u obligatoria." },
      { status: 400 },
    );
  }
  if (json_schema === null || typeof json_schema !== "object" || Array.isArray(json_schema)) {
    return json(
      { error: "validation_error", detail: "El JSON Schema debe ser un objeto JSON válido." },
      { status: 400 },
    );
  }

  const payload = {
    trd_serie_id,
    json_schema,
    version: Number.isInteger(version) && version >= 1 ? version : 1,
    activo: activo !== false,
  };

  try {
    const res = await fetch(`${API_V1}/expediente-metadata/templates`, {
      method: "POST",
      headers: headersFor(locals, true),
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo crear la plantilla de expediente." },
      { status: 503 },
    );
  }
}
