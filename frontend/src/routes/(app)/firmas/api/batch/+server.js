// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /firmas/api/batch — Proxy SSR para firma en lote (D-05, T-12d).
 *
 * POST { solicitud_ids: string[] }
 *   → POST /api/v1/signature/batch
 *   → { resultados: SignatureBatchResult[] }
 *
 * El gateway no aborta ante fallos individuales: responde con resultado por item.
 * Allowlist explícita: solo se reenvía `solicitud_ids` (array no vacío de strings).
 * X-User-Id: trazabilidad del actor (D-05).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  // Allowlist: solo solicitud_ids (anti mass-assignment)
  const { solicitud_ids } = body ?? {};

  if (
    !Array.isArray(solicitud_ids) ||
    solicitud_ids.length === 0 ||
    !solicitud_ids.every((id) => typeof id === "string" && id.trim())
  ) {
    return json(
      {
        error: "validation_error",
        detail: "solicitud_ids debe ser un array no vacío de cadenas de texto.",
      },
      { status: 422 },
    );
  }

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/signature/batch`, {
      method: "POST",
      headers,
      body: JSON.stringify({ solicitud_ids }),
    });

    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de firmas." },
      { status: 503 },
    );
  }
}
