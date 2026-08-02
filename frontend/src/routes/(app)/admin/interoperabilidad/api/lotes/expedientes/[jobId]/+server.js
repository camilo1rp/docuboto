// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/interoperabilidad/api/lotes/expedientes/[jobId] — Proxy SSR de sondeo
 * del job de carga masiva de expedientes (D-05, Fase 8).
 *
 * GET /api/v1/batch/{job_id}/status — mismo contrato y misma regla de sondeo
 * que .../lotes/documentos/[jobId] (nunca sintetizar contadores en 0 ante un
 * fallo de red; distinguir "no se pudo consultar" de "aún sin resultados").
 */

import { API_V1 } from "$lib/utils/config.server.js";
import { json } from "@sveltejs/kit";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  if (!params.jobId || !UUID_RE.test(params.jobId)) {
    return json(
      { error: "validation_error", detail: "Identificador de job inválido." },
      { status: 400 },
    );
  }
  const tenant = locals.user?.tenant_slug ?? "";

  let res;
  try {
    res = await fetch(`${API_V1}/batch/${encodeURIComponent(params.jobId)}/status`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });
  } catch {
    return json(
      {
        error: "connection_error",
        detail: "No se pudo consultar el estado del job (sin conexión).",
      },
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
