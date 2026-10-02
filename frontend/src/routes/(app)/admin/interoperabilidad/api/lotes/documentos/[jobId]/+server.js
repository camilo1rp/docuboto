// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/interoperabilidad/api/lotes/documentos/[jobId] — Proxy SSR de sondeo
 * del job de carga masiva de radicados (D-05, Fase 8).
 *
 * GET /api/v1/batch/documents/{job_id}/status — el estado anida bajo el tipo
 * de job (`documents`) porque document-service y archive-service comparten el
 * prefijo raíz `/api/v1/batch` con formas de `{job_id}/status` idénticas; el
 * gateway (`services/api-gateway/app/routers/proxy.py`) rutea por ese prefijo
 * anidado hacia document-service. Gate real: `PERM_RADI`
 * (`services/document-service/app/routers/batch.py`).
 *
 * Regla de sondeo (Fase 8, punto crítico #3): el cliente SIEMPRE sondea este
 * proxy propio (nunca el gateway directo). Si la consulta de estado falla
 * (red, 5xx, 503), este handler responde con `status: 503`/`error:
 * "connection_error"` para que la UI distinga "no se pudo consultar" de "aún
 * no hay resultados" — nunca sintetiza un job con processed_items=0.
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
  // El backend decide por creador / PERM_RADI nivel 3: exige la identidad.
  const userId = locals.user?.sub ?? "";
  if (!userId) {
    return json(
      { error: "unauthenticated", detail: "Sesión sin identidad de usuario." },
      { status: 401 },
    );
  }

  let res;
  try {
    res = await fetch(`${API_V1}/batch/documents/${encodeURIComponent(params.jobId)}/status`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
        "X-User-Id": userId,
      },
    });
  } catch {
    // Fallo de red: NUNCA se sintetiza un job con contadores en 0 — se
    // distingue explícitamente de "aún no hay resultados" (punto crítico #3).
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
