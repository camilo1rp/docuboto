// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /borradores/api/rag/suggest — Proxy SSR al RAG generativo con citas (D-05,
 * ADR-011, E21 Inc.3, F6 frontend). Gemelo de `/radicar/api/rag/suggest` para la
 * redacción de borradores (Salida/Interno, RichEditor en el drawer de edición).
 *
 * POST → { query: string, top_k?: number }
 *   Respuesta: RagResponse de knowledge-service o { error, detail, status }.
 *
 * Degradación: 503 si knowledge-service no responde — el componente debe tratarlo
 * como "sugerencia no disponible" sin bloquear crear/editar/aprobar/radicar el
 * borrador (regla dura #3 de la tarea).
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

  const query = typeof body?.query === "string" ? body.query.trim().slice(0, 4000) : "";
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

  try {
    const res = await fetch(`${API_V1}/knowledge/rag`, {
      method: "POST",
      headers,
      body: JSON.stringify({ query, top_k: topK }),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar con el servicio de conocimiento." },
      { status: 503 },
    );
  }
}
