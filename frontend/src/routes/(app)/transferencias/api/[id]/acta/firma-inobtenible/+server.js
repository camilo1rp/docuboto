// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /transferencias/api/[id]/acta/firma-inobtenible — Proxy SSR de la
 * declaración de «firma inobtenible» de una parte del acta (D-05, AREA-07-10,
 * ADR-030) — `POST /transferencias/{id}/acta/firma-inobtenible`.
 *
 * Allowlist estricta de campos: `rol`, `motivo`, `fundamento`,
 * `referencia_radicado_id`, `referencia_anexo_id` (obligatorio desde FIRMA-2) y `totp_code?`. La identidad
 * de la parte NUNCA viaja desde aquí: la deriva el servidor del rol. Quién puede
 * declarar (administrador ≥ 3, permiso de firma ≥ 2, no ser parte de la
 * transferencia) lo decide el backend; este proxy solo comprueba forma.
 *
 * `totp_code` se reenvía sin registrarse ni devolverse.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const ROLES = ["entrega", "recibe", "elabora"];
const MOTIVOS = ["fallecimiento", "retiro", "incapacidad", "otro"];
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @param {string} detail */
const invalido = (detail) => json({ error: "validation_error", detail }, { status: 422 });

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

  const { rol, motivo, fundamento, referencia_radicado_id, referencia_anexo_id, totp_code } =
    body ?? {};

  if (!ROLES.includes(rol)) return invalido("El rol de la parte no es válido.");
  if (!MOTIVOS.includes(motivo)) return invalido("El motivo no es válido.");
  const fund = typeof fundamento === "string" ? fundamento.trim() : "";
  if (fund.length < 20 || fund.length > 2000) {
    return invalido("El fundamento debe tener entre 20 y 2000 caracteres.");
  }
  if (typeof referencia_radicado_id !== "string" || !UUID_RE.test(referencia_radicado_id)) {
    return invalido("Elija el radicado que soporta la declaración.");
  }
  if (typeof referencia_anexo_id !== "string" || !UUID_RE.test(referencia_anexo_id)) {
    return invalido("Elija el anexo de soporte de la declaración.");
  }
  const totp = typeof totp_code === "string" ? totp_code.trim() : "";
  if (totp.length > 16) return invalido("El código de verificación es demasiado largo.");

  /** @type {Record<string, unknown>} */
  const payload = {
    rol,
    motivo,
    fundamento: fund,
    referencia_radicado_id,
    referencia_anexo_id,
  };
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
      `${API_V1}/transferencias/${encodeURIComponent(params.id)}/acta/firma-inobtenible`,
      { method: "POST", headers, body: JSON.stringify(payload) },
    );
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo declarar la firma inobtenible." },
      { status: 503 },
    );
  }
}
