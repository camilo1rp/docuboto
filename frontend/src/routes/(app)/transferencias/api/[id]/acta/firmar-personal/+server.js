// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /transferencias/api/[id]/acta/firmar-personal — Proxy SSR de la firma
 * PERSONAL PKI de una parte (elaborador/remitente/receptor) sobre el acta de
 * entrega (D-05, E17/F4) — `POST /transferencias/{id}/acta/firmar-personal`.
 *
 * El ROL se DERIVA en el backend de la identidad congelada
 * (`enviada_por`/`decidida_por`/`creada_por`) — `rol` en el body SOLO
 * desempata el auto-traslado (misma persona envió y recibió); este proxy
 * NUNCA decide el rol, se limita a reenviar lo que el cliente seleccionó
 * entre las opciones que la propia pantalla ya filtró por identidad.
 *
 * `totp_code` viaja tal cual lo introdujo el usuario (backend: 1-32
 * caracteres, `FirmarPersonalRequest.totp_code`) — no se fuerza un formato
 * de 6 dígitos aquí porque el backend no lo exige.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const ROLES_VALIDOS = ["entrega", "recibe", "elabora"];

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

  const { totp_code, rol } = body ?? {};

  if (!totp_code || typeof totp_code !== "string" || !totp_code.trim()) {
    return json(
      { error: "validation_error", detail: "El código de verificación (TOTP) es obligatorio." },
      { status: 422 },
    );
  }
  if (totp_code.trim().length > 32) {
    return json(
      { error: "validation_error", detail: "El código de verificación es demasiado largo." },
      { status: 422 },
    );
  }

  /** @type {Record<string, unknown>} */
  const payload = { totp_code: totp_code.trim() };
  if (rol && typeof rol === "string" && ROLES_VALIDOS.includes(rol)) {
    payload.rol = rol;
  }

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(
      `${API_V1}/transferencias/${encodeURIComponent(id)}/acta/firmar-personal`,
      {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
      },
    );
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo firmar el acta personalmente." },
      { status: 503 },
    );
  }
}
