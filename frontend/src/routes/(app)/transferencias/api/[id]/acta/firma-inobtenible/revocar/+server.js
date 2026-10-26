// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /transferencias/api/[id]/acta/firma-inobtenible/revocar — Proxy SSR de la
 * revocación de la excepción de firma de un rol (D-05, AREA-07-10, ADR-030) —
 * `POST /transferencias/{id}/acta/firma-inobtenible/revocar`.
 *
 * Allowlist: `rol`, `causa` (vocabulario cerrado), `fundamento` (20–2000) y `totp_code?` (firma personal de quien revoca;
 * no se registra ni se devuelve). Misma puerta que declarar; el
 * backend la aplica. La constancia original y su Object-Lock no se tocan: la
 * revocación es una fila nueva.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const ROLES = ["entrega", "recibe", "elabora"];
const CAUSAS = ["error_de_declaracion", "parte_puede_firmar", "otro"];

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params, request }) {
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

  const { rol, fundamento, causa, totp_code } = body ?? {};
  if (!ROLES.includes(rol)) {
    return json(
      { error: "validation_error", detail: "El rol de la parte no es válido." },
      { status: 422 },
    );
  }
  if (!CAUSAS.includes(causa)) {
    return json(
      { error: "validation_error", detail: "Elija la causa de la revocación." },
      { status: 422 },
    );
  }
  const fund = typeof fundamento === "string" ? fundamento.trim() : "";
  if (fund.length < 20 || fund.length > 2000) {
    return json(
      {
        error: "validation_error",
        detail: "El fundamento debe tener entre 20 y 2000 caracteres.",
      },
      { status: 422 },
    );
  }

  const totp = typeof totp_code === "string" ? totp_code.trim() : "";
  if (totp.length > 16) {
    return json(
      { error: "validation_error", detail: "El código de verificación es demasiado largo." },
      { status: 422 },
    );
  }
  /** @type {Record<string, string>} */
  const payload = { rol, causa, fundamento: fund };
  if (totp) payload.totp_code = totp;

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(
      `${API_V1}/transferencias/${encodeURIComponent(params.id)}/acta/firma-inobtenible/revocar`,
      { method: "POST", headers, body: JSON.stringify(payload) },
    );
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo revocar la excepción de firma." },
      { status: 503 },
    );
  }
}
