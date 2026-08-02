// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/interoperabilidad/api/lotes/expedientes — Proxy SSR de carga masiva
 * de expedientes (D-05, Fase 8).
 *
 * POST /api/v1/batch/expedientes — cuerpo {expedientes: BatchExpedienteCreate[]}
 * (1-100). Cada expediente puede traer radicado_ids/tracking_numbers a
 * vincular en la creación (archive-service resuelve ambos). 202 con job_id;
 * progreso vía GET .../[jobId]/status.
 *
 * ADVERTENCIA DE ALCANCE (Fase 8): igual que .../lotes/documentos — el
 * gateway no tiene registrado `/api/v1/batch/` en su tabla de ruteo; ver el
 * comentario allí y el informe de cierre de fase.
 */

import { API_V1 } from "$lib/utils/config.server.js";
import { json } from "@sveltejs/kit";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  if (!userId) {
    return json(
      { error: "unauthenticated", detail: "Sesión sin identidad de usuario." },
      { status: 401 },
    );
  }

  let body = {};
  try {
    body = await request.json();
  } catch {
    return json({ error: "validation_error", detail: "Cuerpo JSON inválido." }, { status: 400 });
  }

  const rawExpedientes = Array.isArray(body.expedientes) ? body.expedientes : [];
  if (rawExpedientes.length < 1 || rawExpedientes.length > 100) {
    return json(
      { error: "validation_error", detail: "Debe incluir entre 1 y 100 expedientes." },
      { status: 400 },
    );
  }

  // Allowlist campo a campo — espejo de BatchExpedienteCreate (archive-service).
  const expedientes = rawExpedientes.map((e) => {
    /** @type {Record<string, unknown>} */
    const item = { name: typeof e?.name === "string" ? e.name.trim() : "" };
    if (typeof e?.description === "string" && e.description.trim())
      item.description = e.description.trim();
    if (typeof e?.trd_serie_id === "string" && UUID_RE.test(e.trd_serie_id))
      item.trd_serie_id = e.trd_serie_id;
    if (Array.isArray(e?.radicado_ids)) {
      item.radicado_ids = e.radicado_ids.filter((id) => typeof id === "string" && UUID_RE.test(id));
    }
    if (Array.isArray(e?.tracking_numbers)) {
      item.tracking_numbers = e.tracking_numbers
        .filter((t) => typeof t === "string" && t.trim())
        .map((t) => t.trim());
    }
    return item;
  });

  const invalid = expedientes.find((e) => !e.name || e.name.length > 500);
  if (invalid) {
    return json(
      {
        error: "validation_error",
        detail: "Cada expediente requiere un nombre (1-500 caracteres).",
      },
      { status: 400 },
    );
  }

  let res;
  try {
    res = await fetch(`${API_V1}/batch/expedientes`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
        "X-User-Id": userId,
      },
      body: JSON.stringify({ expedientes }),
    });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de carga masiva." },
      { status: 503 },
    );
  }

  const data = await res.json().catch(() => ({
    error: "upstream_error",
    detail: `HTTP ${res.status}`,
    status: res.status,
  }));
  return json(data, { status: res.status });
}
