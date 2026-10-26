// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /bandeja/api/[id]/anulacion — Proxy SSR: solicitud de anulación de radicado (D-05, ADR-011, T-08b).
 *
 * El token y el userId se leen de event.locals (cookie httpOnly + claims del JWT).
 * El JS del cliente NUNCA ve el token. El backend re-valida RBAC y tenant.
 *
 * Autorización: la compuerta autoritativa es el backend + gateway. El gateway re-deriva
 * X-User-Id y X-Tenant-Slug del JWT firmado, no son falsificables desde el cliente.
 * El gating de permiso fino (permissions[]) no se aplica aquí porque locals.user.permissions
 * llega como [] en las rutas de API (se hidrata solo en layout.server.js → data.user).
 * La UI ya filtra las acciones por $permissions; el backend re-valida RBAC en cada operación.
 *
 * POST → solicita anulación al document-service.
 *   Body (AnulacionSolicitud): { causal: string (≤15), motivo?: string (≤2000) }
 *   Respuesta: AnulacionSolicitudResponse o { error, detail, status }
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

  const { causal, motivo } = body ?? {};

  // Validación mínima antes de reenviar (evita round-trip innecesario)
  if (!causal || typeof causal !== "string" || !causal.trim()) {
    return json(
      { error: "validation_error", detail: "El campo 'causal' es obligatorio." },
      { status: 422 },
    );
  }
  if (causal.trim().length > 15) {
    return json(
      { error: "validation_error", detail: "El campo 'causal' no puede superar 15 caracteres." },
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
  const payload = { causal: causal.trim() };
  if (motivo && typeof motivo === "string" && motivo.trim()) {
    payload.motivo = motivo.trim().slice(0, 2000);
  }

  try {
    const res = await fetch(`${API_V1}/documents/${id}/anulacion`, {
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
      { error: "connection_error", detail: "No se pudo solicitar la anulación." },
      { status: 503 },
    );
  }
}
