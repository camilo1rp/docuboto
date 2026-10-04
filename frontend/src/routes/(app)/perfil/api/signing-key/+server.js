// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /perfil/api/signing-key — Proxy SSR del estado y la auto-revocación de la
 * credencial de firma personal PKI (E17/F4, D-05).
 *
 * GET → GET /api/v1/auth/me/signing-key
 *   → { estado, cert_pem, serial, subject_dn, not_before, not_after, activated_at }
 *   NUNCA la clave privada — ni este proxy ni el backend la exponen jamás.
 *
 * DELETE { step_up_totp_code, motivo? } → DELETE /api/v1/auth/me/signing-key
 *   Auto-revocación: el backend SIEMPRE exige `step_up_totp_code` aquí
 *   (a diferencia del re-enroll, que solo lo exige sobre una credencial
 *   'activo') — se valida la FORMA (6 dígitos) server-side, la vigencia
 *   la decide auth-service.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { motivoRevocacionValido } from "$lib/utils/motivoRevocacion.js";

const CODE_RE = /^\d{6}$/;

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  try {
    const res = await fetch(`${API_V1}/auth/me/signing-key`, {
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
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  // Allowlist: solo los dos campos del contrato (anti mass-assignment).
  const { step_up_totp_code, motivo } = body ?? {};
  if (typeof step_up_totp_code !== "string" || !CODE_RE.test(step_up_totp_code)) {
    return json(
      {
        error: "validation_error",
        detail: "Se requiere el código de 6 dígitos del segundo factor para revocar la credencial.",
      },
      { status: 422 },
    );
  }

  /** @type {Record<string, unknown>} */
  const payload = { step_up_totp_code };
  // Vocabulario cerrado: un valor fuera de él se descarta (el backend re-valida).
  const motivoCodigo = motivoRevocacionValido(motivo);
  if (motivoCodigo) {
    payload.motivo = motivoCodigo;
  }

  try {
    const res = await fetch(`${API_V1}/auth/me/signing-key`, {
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
