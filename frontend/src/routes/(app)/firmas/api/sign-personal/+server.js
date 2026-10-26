// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /firmas/api/sign-personal — Proxy SSR de la firma PERSONAL PKI por-usuario
 * (E17/F4, ADR-016, D-05) — `POST /api/v1/signature/sign-personal`.
 *
 * Firma SIEMPRE con el certificado personal del actor autenticado (nunca el
 * sello institucional) — requiere credencial 'activo' en auth-service y un
 * `totp_code` válido, verificado-y-consumido atómicamente junto con la
 * firma. El firmante viene del JWT (RF-FIR-11); `firmante_id` en el body es
 * puramente defensivo (el backend exige que coincida si se declara — este
 * proxy nunca lo rellena, lo deja ausente salvo que el llamador lo pase).
 *
 * POST { payload_xml, objeto_tipo, objeto_id, totp_code, indice_id?, version? }
 *   → FirmaResponse (201)
 *
 * El front no compone criptografía ni maneja material privado: solo
 * reenvía payload_xml + totp_code (introducido por el usuario) al gateway.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const TOTP_RE = /^\d{6}$/;

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  const authorization = `Bearer ${token}`;

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const { payload_xml, objeto_tipo, objeto_id, totp_code, indice_id, version } = body ?? {};

  if (!payload_xml || typeof payload_xml !== "string" || !payload_xml.trim()) {
    return json(
      { error: "validation_error", detail: "payload_xml es obligatorio." },
      { status: 422 },
    );
  }
  if (!objeto_tipo || typeof objeto_tipo !== "string" || !objeto_tipo.trim()) {
    return json(
      { error: "validation_error", detail: "objeto_tipo es obligatorio." },
      { status: 422 },
    );
  }
  if (!objeto_id || typeof objeto_id !== "string" || !objeto_id.trim()) {
    return json(
      { error: "validation_error", detail: "objeto_id es obligatorio." },
      { status: 422 },
    );
  }
  if (!totp_code || typeof totp_code !== "string" || !TOTP_RE.test(totp_code)) {
    return json(
      { error: "validation_error", detail: "totp_code debe tener exactamente 6 dígitos." },
      { status: 422 },
    );
  }

  /** @type {Record<string, unknown>} */
  const payload = { payload_xml, objeto_tipo, objeto_id, totp_code };
  if (indice_id) payload.indice_id = String(indice_id);
  if (version !== undefined && version !== null) payload.version = Number(version);

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: authorization,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/signature/sign-personal`, {
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
