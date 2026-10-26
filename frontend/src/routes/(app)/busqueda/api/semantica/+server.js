// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /busqueda/api/semantica — Proxy SSR de búsqueda semántica (D-05, Fase 6 E21↔UI).
 *
 * Endpoint real: POST /api/v1/knowledge/search (services/knowledge-service/app/routers/knowledge.py:188).
 *   Body: { query, top_k, acl } — `acl` es un filtro interno de metadata (no lo
 *   expone la UI: solo `query`/`top_k` pasan la allowlist, así el cliente no
 *   puede inyectar un filtro de acceso propio).
 *   Respuesta real: `list[SearchHit]` — { id, source_type, source_ref, texto,
 *   metadata, score } — SIN envoltorio. Se envuelve aquí en `{ results }` para
 *   que el cliente distinga siempre "petición ok, 0 aciertos" de "petición falló"
 *   por el código HTTP, igual que el resto de proxies de esta pantalla.
 *
 * El pre-filtro ACL (fail-closed, por `X-User-Id`) lo aplica knowledge-service
 * server-side — un resultado que el usuario NO puede ver simplemente no llega
 * aquí; este proxy no debe (ni puede) distinguir "sin resultados" de "resultados
 * ocultos por permisos": esa distinción NO existe de este lado (anti-oráculo).
 *
 * `score` es una similitud vectorial (0–1, coseno), NO una coincidencia exacta
 * de texto — la UI es responsable de explicarlo, este proxy solo relaya el dato.
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
  if (query.length < 1) {
    return json(
      { error: "validation_error", detail: "La búsqueda semántica requiere un texto." },
      { status: 422 },
    );
  }

  // top_k: entero 1-50 (mismo límite que SearchRequest.top_k del backend); se
  // acota aquí también para no depender solo de la validación remota.
  const rawTopK = Number(body?.top_k);
  const topK = Number.isFinite(rawTopK) ? Math.min(50, Math.max(1, Math.floor(rawTopK))) : 5;

  // Allowlist campo a campo — `acl` NUNCA sale del cliente hacia el gateway.
  const payload = { query, top_k: topK };

  try {
    const res = await fetch(`${API_V1}/knowledge/search`, {
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

    return json({ results: Array.isArray(data) ? data : [] });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servicio de conocimiento." },
      { status: 503 },
    );
  }
}
