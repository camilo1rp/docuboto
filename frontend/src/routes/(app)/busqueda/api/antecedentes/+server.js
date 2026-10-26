// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /busqueda/api/antecedentes — Proxy SSR de antecedentes (D-05, Fase 6 E21↔UI).
 *
 * Endpoint real: POST /api/v1/knowledge/antecedentes
 *   (services/knowledge-service/app/routers/knowledge.py:274).
 *   Body (AntecedentesRequest): exactamente uno de `query`/`radicado_ref` (XOR),
 *   `source_type` (default "radicado"), `top_k` (1-20, default 5).
 *   Respuesta real: { antecedentes: [{ source_type, source_ref, snippet, score }],
 *   advisory } — SIN envoltorio adicional; se relaya tal cual.
 *
 * El XOR se valida aquí TAMBIÉN (antes de tocar el gateway) para no gastar una
 * llamada en un 422 previsible — el backend es la fuente de verdad, esto es
 * solo un filtro rápido.
 *
 * El pivote por `radicado_ref` colapsa "no indexado" y "clearance insuficiente"
 * en el MISMO 404 genérico (anti-oráculo, ver `_antecedentes_source_not_found`
 * en el backend) — este proxy relaya ese 404 sin enriquecerlo.
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

  const query = typeof body?.query === "string" ? body.query.trim() : "";
  const radicadoRef = typeof body?.radicado_ref === "string" ? body.radicado_ref.trim() : "";

  // XOR: exactamente uno de los dos modos de búsqueda de antecedentes.
  if (query.length > 0 === radicadoRef.length > 0) {
    return json(
      {
        error: "validation_error",
        detail: "Indique un texto de búsqueda o un número de radicado (no ambos).",
      },
      { status: 422 },
    );
  }

  const rawTopK = Number(body?.top_k);
  const topK = Number.isFinite(rawTopK) ? Math.min(20, Math.max(1, Math.floor(rawTopK))) : 5;

  // Allowlist campo a campo — solo uno de query/radicado_ref viaja, nunca ambos.
  const payload =
    query.length > 0 ? { query, top_k: topK } : { radicado_ref: radicadoRef, top_k: topK };

  // source_type: solo "radicado" por ahora (único caso de uso de esta pantalla);
  // no se expone como campo libre del cliente para no ampliar el contrato sin
  // necesidad real.

  try {
    const res = await fetch(`${API_V1}/knowledge/antecedentes`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
        "X-User-Id": userId,
      },
      body: JSON.stringify(payload),
    });

    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      return json(data, { status: res.status });
    }

    return json(data);
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servicio de conocimiento." },
      { status: 503 },
    );
  }
}
