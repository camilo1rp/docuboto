// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /perfil/api/totp — Proxy SSR del estado y la revocación del segundo
 * factor TOTP propio (E06/RFC 6238, D-05).
 *
 * GET    → GET /api/v1/auth/me/totp            (estado, NUNCA el secreto)
 * DELETE { step_up_totp_code? } → DELETE /api/v1/auth/me/totp
 *   El backend exige `step_up_totp_code` SOLO si el estado actual es
 *   'activo' (TotpRevokeRequest) — este proxy no lo hace obligatorio a
 *   priori: reenvía lo que llegue y deja que el backend decida (mismo
 *   estado que ya audita/verifica). Allowlist de un solo campo.
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

  try {
    const res = await fetch(`${API_V1}/auth/me/totp`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
        ...(userId ? { "X-User-Id": userId } : {}),
      },
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

/** @type {import('./$types').RequestHandler} */
export async function DELETE({ locals, request }) {
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

  // Allowlist: los dos factores del contrato, campo a campo. El código de
  // recuperación existe precisamente para este endpoint — revocar el segundo
  // factor cuando se perdió el dispositivo que lo genera.
  const { step_up_totp_code, step_up_recovery_code } = body ?? {};
  /** @type {Record<string, unknown>} */
  const payload = {};
  if (typeof step_up_totp_code === "string" && step_up_totp_code.trim()) {
    payload.step_up_totp_code = step_up_totp_code.trim();
  }
  if (typeof step_up_recovery_code === "string" && step_up_recovery_code.trim()) {
    payload.step_up_recovery_code = step_up_recovery_code.trim();
  }

  try {
    const res = await fetch(`${API_V1}/auth/me/totp`, {
      method: "DELETE",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
        ...(userId ? { "X-User-Id": userId } : {}),
      },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de autenticación." },
      { status: 503 },
    );
  }
}
