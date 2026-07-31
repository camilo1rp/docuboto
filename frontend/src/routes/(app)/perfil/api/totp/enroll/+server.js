// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /perfil/api/totp/enroll — Proxy SSR para (re)enrolar TOTP.
 *
 * POST { step_up_totp_code? } → POST /api/v1/auth/me/totp/enroll
 *   → { otpauth_uri, estado: 'pendiente_activacion' }
 *
 * `otpauth_uri` embebe el secreto TOTP EN CLARO — se revela una sola vez.
 * El backend ya marca la respuesta `Cache-Control: no-store`; este proxy
 * la reafirma explícitamente por si algún intermediario del lado servidor
 * la perdiera al reconstruir la respuesta con `json()`.
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
    body = {};
  }

  // Allowlist: un solo campo del contrato.
  const { step_up_totp_code } = body ?? {};
  /** @type {Record<string, unknown>} */
  const payload = {};
  if (typeof step_up_totp_code === "string" && step_up_totp_code.trim()) {
    payload.step_up_totp_code = step_up_totp_code.trim();
  }

  try {
    const res = await fetch(`${API_V1}/auth/me/totp/enroll`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
        ...(userId ? { "X-User-Id": userId } : {}),
      },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    const response = json(data, { status: res.status });
    response.headers.set("Cache-Control", "no-store");
    return response;
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de autenticación." },
      { status: 503 },
    );
  }
}
