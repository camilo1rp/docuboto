// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /firmas/api/[id]/challenge — Proxy SSR para emitir el reto OTP por correo
 * del segundo factor (E06 Inc.2, RF-FIR-13, D-05).
 *
 * POST (sin cuerpo — el reto se emite para EL TURNO ACTIVO del actor actual,
 * resuelto en el backend a partir de X-User-Id, nunca de un id que el
 * cliente pudiera elegir)
 *   → POST /api/v1/signature/requests/{id}/challenge
 *   → ChallengeResponse { challenge_id, factor_tipo, canal (correo enmascarado), expira_at }
 *
 * El backend ata el desafío a la huella del documento cuando la cadena la fija,
 * y al identificador del objeto cuando el tipo se firma como conjunto
 * (`radicado`, `expediente`) — antes exigía la huella siempre y esas cadenas no
 * se podían firmar. Aplica además un cooldown de reenvío (429
 * challenge_cooldown) — este proxy NO reintenta ni oculta esos códigos: los
 * reenvía tal cual para que la UI pueda explicar al usuario qué pasó y qué
 * hacer (ver SignAssistant.svelte).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(
      `${API_V1}/signature/requests/${encodeURIComponent(params.id)}/challenge`,
      { method: "POST", headers, body: JSON.stringify({}) },
    );

    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de firmas." },
      { status: 503 },
    );
  }
}
