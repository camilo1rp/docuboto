// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /perfil/api/signing-key/enroll — Proxy SSR para (re)enrolar la credencial
 * de firma personal PKI (E17/F4).
 *
 * POST { step_up_totp_code? } → POST /api/v1/auth/me/signing-key/enroll
 *   → { estado: 'pendiente_emision', csr_pem }
 *
 * El par de claves se genera y custodia EN EL SERVIDOR (auth-service); esta
 * ruta nunca ve ni transporta material privado — csr_pem es información
 * pública. `step_up_totp_code` solo es obligatorio cuando ya existe una
 * credencial 'activo' (el backend responde 409 `signing_key_activa_requiere_step_up`
 * si falta) — un re-enroll sobre una credencial activa la REVOCA de
 * inmediato; la pantalla (perfil/+page.svelte) exige una confirmación
 * tecleada antes de siquiera intentar esta llamada cuando ese es el caso.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const CODE_RE = /^\d{6}$/;

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
    if (!CODE_RE.test(step_up_totp_code.trim())) {
      return json(
        { error: "validation_error", detail: "El código debe tener exactamente 6 dígitos." },
        { status: 422 },
      );
    }
    payload.step_up_totp_code = step_up_totp_code.trim();
  }

  try {
    const res = await fetch(`${API_V1}/auth/me/signing-key/enroll`, {
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
