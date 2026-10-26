// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/interoperabilidad/api/lotes/documentos/[jobId]/reprocesar — Proxy SSR
 * para reanudar un lote de radicación con errores (D-05).
 *
 * POST /api/v1/batch/documents/{job_id}/reprocesar — sin cuerpo, gate real
 * `PERM_RADI` (document-service). 202 `{job_id, status:"pending",
 * items_a_procesar}`; 404 `lote_no_encontrado`; 409 `lote_en_proceso` /
 * `nada_que_reprocesar`. Idempotente: no renumera lo ya radicado.
 *
 * Allowlist: el único dato que el cliente controla es el `jobId` (UUID); no se
 * reenvía cuerpo, query ni cabeceras del navegador.
 */

import { API_V1 } from "$lib/utils/config.server.js";
import { json } from "@sveltejs/kit";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  if (!params.jobId || !UUID_RE.test(params.jobId)) {
    return json(
      { error: "validation_error", detail: "Identificador de lote inválido." },
      { status: 400 },
    );
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  if (!userId) {
    return json(
      { error: "unauthenticated", detail: "Sesión sin identidad de usuario." },
      { status: 401 },
    );
  }

  let res;
  try {
    res = await fetch(`${API_V1}/batch/documents/${encodeURIComponent(params.jobId)}/reprocesar`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
        "X-User-Id": userId,
      },
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
