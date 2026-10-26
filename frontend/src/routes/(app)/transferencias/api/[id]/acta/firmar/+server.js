// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /transferencias/api/[id]/acta/firmar — Proxy SSR del reintento MANUAL del
 * sellado institucional XAdES del acta de entrega (D-05, H-H E06/F4) —
 * `POST /transferencias/{id}/acta/firmar`.
 *
 * Gemelo INSTITUCIONAL de `acta/firmar-personal` — este NO es un acto de
 * firma personal PKI de una parte, es el reintento del SELLO del sistema
 * cuando el sellado automático al recibir la transferencia quedó en
 * 'pendiente_firma'/'agotado' (backoff). No requiere TOTP.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

export async function POST({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  const { id } = params;

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/transferencias/${encodeURIComponent(id)}/acta/firmar`, {
      method: "POST",
      headers,
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo reintentar el sellado del acta." },
      { status: 503 },
    );
  }
}
