// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/notificaciones/api/webhooks — Proxy SSR: CRUD de suscripciones de
 * webhooks salientes (`services/notification-service/app/routers/webhooks.py`,
 * E11/ADR-018, USUA_PERM_ADMIN a nivel de router, D-05).
 *
 * POST allowlist campo a campo (nunca el body íntegro): { url, event_types?,
 * secret? } — espejo exacto de `WebhookCreate`. Registrar una suscripción
 * entrega CADA evento del tenant a una URL externa (canal de exfiltración /
 * SSRF si cualquiera pudiera crearla sin gate); este proxy no añade un guard
 * de permiso propio, se limita a propagar el status/detail del gateway.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/webhooks`, { headers });
    const data = await res.json().catch(() => []);
    return json(data, {
      status: res.status,
      headers: { "X-Total-Count": res.headers.get("X-Total-Count") ?? "0" },
    });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de webhooks." },
      { status: 503 },
    );
  }
}

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

  const { url: targetUrl, event_types: rawEventTypes, secret } = body ?? {};

  if (typeof targetUrl !== "string" || !targetUrl.trim()) {
    return json(
      { error: "validation_error", detail: "La URL de destino es obligatoria." },
      { status: 422 },
    );
  }

  const eventTypes = Array.isArray(rawEventTypes)
    ? rawEventTypes.filter((t) => typeof t === "string" && t.trim()).map((t) => t.trim())
    : [];

  // Allowlist campo a campo — nunca se reenvía `body` íntegro.
  const payload = {
    url: targetUrl.trim(),
    event_types: eventTypes,
    secret: typeof secret === "string" && secret.trim() ? secret.trim().slice(0, 255) : null,
  };

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/webhooks`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo crear el webhook." },
      { status: 503 },
    );
  }
}
