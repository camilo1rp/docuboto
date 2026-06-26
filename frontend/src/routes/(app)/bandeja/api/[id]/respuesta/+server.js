// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /bandeja/api/[id]/respuesta — Proxy SSR: crea Salida vinculada a un radicado de Entrada (D-05, ADR-011, T-08b).
 *
 * El token y el userId se leen de event.locals (cookie httpOnly + claims del JWT).
 * El JS del cliente NUNCA ve el token. El backend re-valida RBAC y genera el número de radicado de Salida.
 *
 * Autorización: la compuerta autoritativa es el backend + gateway. El gateway re-deriva
 * X-User-Id y X-Tenant-Slug del JWT firmado, no son falsificables desde el cliente.
 * El gating de permiso fino (permissions[]) no se aplica aquí porque locals.user.permissions
 * llega como [] en las rutas de API (se hidrata solo en layout.server.js → data.user).
 * La UI ya filtra las acciones por $permissions; el backend re-valida RBAC en cada operación.
 *
 * POST → crea el radicado de Salida vinculado en el document-service.
 *   Body (RespuestaRapidaCreate): {
 *     subject?: string,
 *     dest_dept?: string,
 *     dest_dept_code?: number,
 *     observations?: string,
 *     response_days?: number,
 *     metadata?: Record<string, unknown>
 *   }
 *   Header requerido: X-User-Id.
 *   Respuesta: DocumentResponse (la Salida creada) o { error, detail, status }
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  const { id } = params;

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const { subject, dest_dept, dest_dept_code, observations, response_days, metadata } = body ?? {};

  // Construir payload — todos los campos son opcionales en RespuestaRapidaCreate
  /** @type {Record<string, unknown>} */
  const payload = {};
  if (subject && typeof subject === "string" && subject.trim()) {
    payload.subject = subject.trim().slice(0, 255);
  }
  if (dest_dept && typeof dest_dept === "string" && dest_dept.trim()) {
    payload.dest_dept = dest_dept.trim();
  }
  if (dest_dept_code !== undefined && dest_dept_code !== null && dest_dept_code !== "") {
    const code = Number(dest_dept_code);
    if (!isNaN(code) && code > 0) payload.dest_dept_code = code;
  }
  if (observations && typeof observations === "string" && observations.trim()) {
    payload.observations = observations.trim().slice(0, 2000);
  }
  if (response_days !== undefined && response_days !== null && response_days !== "") {
    const days = Number(response_days);
    if (!isNaN(days) && days > 0) payload.response_days = days;
  }
  if (metadata && typeof metadata === "object") {
    payload.metadata = metadata;
  }

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/documents/${id}/respuesta`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });

    if (res.status === 204) {
      return json({ ok: true }, { status: 200 });
    }
    const data = await res.json().catch(() => ({ ok: true }));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo crear el radicado de respuesta." },
      { status: 503 },
    );
  }
}
