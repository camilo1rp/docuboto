// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/metadatos/api/document-templates — Proxy SSR: plantillas de
 * metadatos de DOCUMENTO/radicado (D-05, E03/ADR-007) —
 * services/document-service/app/routers/metadata.py.
 *
 * GET  ?tipo_documental= → MetadataTemplateResponse[].
 * POST                    → crea una plantilla. Body: { tipo_documental,
 *   json_schema, version?, activo? }.
 *
 * HALLAZGO DE DOMINIO (ver +page.server.js): este router NO tiene
 * `require_permission` en el backend — cualquier llamado autenticado del
 * tenant puede escribir. El proxy no inventa una autorización que el
 * backend no aplica; la UI gatea igualmente el botón con USUA_PERM_ADMIN
 * como medida conservadora (más restrictivo que el backend nunca es inseguro).
 *
 * Sin PATCH/DELETE en el backend — igual que expediente-templates, una
 * nueva versión es un nuevo registro.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

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
  const tipo = url.searchParams.get("tipo_documental");
  if (typeof tipo === "string" && tipo.trim()) params.set("tipo_documental", tipo.trim().slice(0, 100));

  try {
    const res = await fetch(`${API_V1}/metadata/templates?${params}`, { headers: headersFor(locals) });
    const data = await res.json().catch(() => []);
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de plantillas de documento." },
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

  const { tipo_documental, json_schema, version, activo } = body ?? {};

  if (typeof tipo_documental !== "string" || !tipo_documental.trim()) {
    return json({ error: "validation_error", detail: "El tipo documental es obligatorio." }, { status: 400 });
  }
  if (json_schema === null || typeof json_schema !== "object" || Array.isArray(json_schema)) {
    return json(
      { error: "validation_error", detail: "El JSON Schema debe ser un objeto JSON válido." },
      { status: 400 },
    );
  }

  const payload = {
    tipo_documental: tipo_documental.trim().slice(0, 100),
    json_schema,
    version: Number.isInteger(version) && version >= 1 ? version : 1,
    activo: activo !== false,
  };

  try {
    const res = await fetch(`${API_V1}/metadata/templates`, {
      method: "POST",
      headers: headersFor(locals, true),
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json({ error: "connection_error", detail: "No se pudo crear la plantilla de documento." }, { status: 503 });
  }
}
