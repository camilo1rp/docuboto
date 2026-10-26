// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /radicar/api/rag/antecedentes — Proxy SSR de precedentes citados (D-05, ADR-011,
 * E21 Inc.2 Patrón A) + resolución del tipo documental sugerido (chip junto al
 * selector de clasificación del paso 1 de radicación).
 *
 * `knowledge-service::POST /antecedentes` NO devuelve `doc_class` (su contrato
 * real es solo source_type/source_ref/snippet/score, ver
 * services/knowledge-service/app/routers/knowledge.py:242) — este proxy NO
 * inventa ese campo: lo resuelve consultando `GET /documents/by-tracking/{tracking}`
 * (mismo gateway, mismo clearance del llamante) para cada precedente de tipo
 * "radicado" y agrega por `doc_class`. Si un precedente no resuelve (404/])
 * simplemente no aporta al conteo — no es un error del flujo.
 *
 * REGLA DURA #2 (no negociable, ver prompt de la tarea): esto es una SUGERENCIA.
 * Este proxy no aplica nada; devuelve `suggestions[]` para que el usuario decida
 * si pulsa el chip. `doc_class` (tipo documental) es el selector de clasificación
 * real que existe en el paso 1 de `/radicar` — no existe un endpoint que sugiera
 * `trd_serie_id` directamente sobre un radicado nuevo (los radicados no cargan
 * `trd_serie_id`; esa clasificación vive en el expediente), así que NO se inventa
 * esa correlación.
 *
 * POST → { query: string, top_k?: number }
 *   Respuesta: { suggestions: [{doc_class, count, best_score, example_ref}],
 *                antecedentes: Antecedente[], advisory } o { error, detail, status }.
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

  const query = typeof body?.query === "string" ? body.query.trim().slice(0, 2000) : "";
  if (query.length < 2) {
    return json(
      { error: "validation_error", detail: "La consulta debe tener al menos 2 caracteres." },
      { status: 422 },
    );
  }
  const topK = Number.isInteger(body?.top_k) && body.top_k > 0 && body.top_k <= 20 ? body.top_k : 5;

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
    "X-Canal": "ui",
  };
  if (userId) headers["X-User-Id"] = userId;

  let antecedentesRes;
  try {
    antecedentesRes = await fetch(`${API_V1}/knowledge/antecedentes`, {
      method: "POST",
      headers,
      body: JSON.stringify({ query, top_k: topK }),
    });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar con el servicio de conocimiento." },
      { status: 503 },
    );
  }

  const data = await antecedentesRes.json().catch(() => ({}));
  if (!antecedentesRes.ok) {
    return json(data, { status: antecedentesRes.status });
  }

  const antecedentes = Array.isArray(data.antecedentes) ? data.antecedentes : [];

  const docHeaders = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
    "X-Canal": "ui",
  };
  if (userId) docHeaders["X-User-Id"] = userId;

  const lookups = await Promise.allSettled(
    antecedentes
      .filter((a) => a?.source_type === "radicado" && typeof a.source_ref === "string")
      .map(async (a) => {
        const res = await fetch(
          `${API_V1}/documents/by-tracking/${encodeURIComponent(a.source_ref)}`,
          { headers: docHeaders },
        );
        if (!res.ok) return null;
        const doc = await res.json().catch(() => null);
        return doc?.doc_class
          ? { source_ref: a.source_ref, score: a.score, doc_class: doc.doc_class }
          : null;
      }),
  );

  /** @type {Map<string, {doc_class: string, count: number, best_score: number, example_ref: string}>} */
  const byClass = new Map();
  for (const r of lookups) {
    if (r.status !== "fulfilled" || !r.value) continue;
    const { doc_class, score, source_ref } = r.value;
    const prev = byClass.get(doc_class);
    if (!prev) {
      byClass.set(doc_class, { doc_class, count: 1, best_score: score, example_ref: source_ref });
    } else {
      prev.count += 1;
      if (score > prev.best_score) {
        prev.best_score = score;
        prev.example_ref = source_ref;
      }
    }
  }

  const suggestions = Array.from(byClass.values()).sort(
    (a, b) => b.count - a.count || b.best_score - a.best_score,
  );

  return json({ suggestions, antecedentes, advisory: data.advisory ?? null }, { status: 200 });
}
