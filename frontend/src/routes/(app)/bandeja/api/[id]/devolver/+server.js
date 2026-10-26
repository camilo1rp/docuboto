// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /bandeja/api/[id]/devolver — Proxy SSR: devolución de radicado en flujo (D-05, ADR-011, T-08b).
 *
 * El token y el userId se leen de event.locals (cookie httpOnly + claims del JWT).
 * El JS del cliente NUNCA ve el token. El backend re-valida RBAC, tenant y estado del flujo.
 *
 * Autorización: la compuerta autoritativa es el backend + gateway. El gateway re-deriva
 * X-User-Id y X-Tenant-Slug del JWT firmado, no son falsificables desde el cliente.
 * El gating de permiso fino (permissions[]) no se aplica aquí porque locals.user.permissions
 * llega como [] en las rutas de API (se hidrata solo en layout.server.js → data.user).
 * La UI ya filtra las acciones por $permissions; el backend re-valida RBAC en cada operación.
 *
 * POST → devuelve el radicado al workflow-service.
 *   Body (DevolucionRequest): { to_dept_code: int (ADR-032), causal: string (≤15), comentario?: string }
 *   Headers requeridos por el backend: X-Tenant-Slug y X-User-Id.
 *   Respuesta: WorkflowEventResponse o { error, detail, status }
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { parseCodigoDependencia } from "$lib/utils/dependenciaNombre.js";

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

  const { to_dept_code, causal, comentario } = body ?? {};

  // Validación mínima antes de reenviar
  const toDeptCode = parseCodigoDependencia(to_dept_code);
  if (toDeptCode === null) {
    return json(
      {
        error: "validation_error",
        detail: "Seleccione la dependencia destino del catálogo.",
      },
      { status: 422 },
    );
  }
  if (!causal || typeof causal !== "string" || !causal.trim()) {
    return json(
      { error: "validation_error", detail: "Seleccione la causal de la devolución." },
      { status: 422 },
    );
  }
  if (causal.trim().length > 15) {
    return json(
      { error: "validation_error", detail: "La causal seleccionada no es válida." },
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

  /** @type {Record<string, unknown>} */
  const payload = {
    to_dept_code: toDeptCode,
    causal: causal.trim(),
  };
  if (comentario && typeof comentario === "string" && comentario.trim()) {
    payload.comentario = comentario.trim().slice(0, 2000);
  }

  try {
    const res = await fetch(`${API_V1}/workflows/${id}/devolver`, {
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
      { error: "connection_error", detail: "No se pudo devolver el radicado." },
      { status: 503 },
    );
  }
}
