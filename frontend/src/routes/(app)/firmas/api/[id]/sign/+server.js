// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /firmas/api/[id]/sign — Proxy SSR para firmar el turno activo (D-05, T-12d,
 * ampliado con el segundo factor E06 Inc.2/RF-FIR-13).
 *
 * POST { challenge_id?, otp?, totp_code? } (todos opcionales — un objeto de
 * seguridad PUBLICA sin `settings.firma_2fa_required` puede firmar sin
 * segundo factor; RESERVADA/CLASIFICADA SIEMPRE lo exige, ver
 * cadena_service.firmar)
 *   → POST /api/v1/signature/requests/{id}/sign
 *   → SolicitudResponse actualizada.
 *
 * Errores del gateway que la UI debe distinguir (ver SignAssistant.svelte):
 *   403 no_es_su_turno | no_es_firmante  — el turno ya no corresponde al usuario
 *   409 solicitud_no_pendiente           — la solicitud ya fue resuelta
 *   409 2fa_requerido                    — falta iniciar el reto OTP/TOTP
 *   409 challenge_invalido|_ajeno|_acto_distinto|_documento_distinto|_consumido|_expirado|_bloqueado
 *   401 otp_incorrecto                   — trae `intentos_restantes`
 *
 * Allowlist explícita: solo challenge_id (string), otp (string), totp_code
 * (string) — nunca el body íntegro. `payload_xml` NO se expone en esta UI
 * (el front nunca compone el material a firmar; el backend ya fija
 * `payload_sha256` al crear la solicitud).
 * X-User-Id: trazabilidad del actor (D-05).
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

  let body = {};
  try {
    const raw = await request.text();
    body = raw ? JSON.parse(raw) : {};
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  // Allowlist: solo el segundo factor, nunca el body íntegro (anti mass-assignment).
  const { challenge_id, otp, totp_code } = body ?? {};

  /** @type {Record<string, unknown>} */
  const payload = {};
  if (challenge_id !== undefined && challenge_id !== null) {
    if (typeof challenge_id !== "string" || !challenge_id.trim()) {
      return json({ error: "validation_error", detail: "challenge_id inválido." }, { status: 422 });
    }
    payload.challenge_id = challenge_id;
  }
  if (otp !== undefined && otp !== null) {
    if (typeof otp !== "string" || !otp.trim()) {
      return json({ error: "validation_error", detail: "otp inválido." }, { status: 422 });
    }
    payload.otp = otp;
  }
  if (totp_code !== undefined && totp_code !== null) {
    if (typeof totp_code !== "string" || !totp_code.trim()) {
      return json({ error: "validation_error", detail: "totp_code inválido." }, { status: 422 });
    }
    payload.totp_code = totp_code;
  }

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/signature/requests/${encodeURIComponent(params.id)}/sign`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });

    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de firmas." },
      { status: 503 },
    );
  }
}
