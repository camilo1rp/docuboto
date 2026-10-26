// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Proxy SSR de `POST /api/v1/auth/me/totp/recovery-codes` — regenera el lote de
 * códigos de recuperación del segundo factor.
 *
 * D-05: el token vive solo en `locals`; el navegador nunca lo ve. La identidad
 * (`X-Tenant-Slug`/`X-User-Id`) se deriva de la sesión, nunca del cuerpo.
 *
 * La respuesta trae los códigos **en claro** — la única vez que existen fuera
 * de la memoria del usuario, porque el backend solo guarda su hash. Por eso se
 * fuerza `Cache-Control: no-store` también en este tramo: el backend ya lo
 * envía, pero esta respuesta la construye SvelteKit de nuevo y la cabecera del
 * upstream no viaja sola.
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

  // Allowlist campo a campo: los dos factores admitidos, nada más.
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
    const res = await fetch(`${API_V1}/auth/me/totp/recovery-codes`, {
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
