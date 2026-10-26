// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/parametros/api/asistente — Proxy SSR: bandera del asistente por entidad
 * (C-1, ADR-037) — GET/PUT /config/asistente.
 *
 * El PUT exige `USUA_PERM_ADMIN` nivel >= 3 (403 `forbidden` que nombra el nivel); el
 * backend re-valida siempre, la pantalla solo lo espeja. Allowlist: solo viaja
 * `habilitado` (booleano estricto), `declaracion_transmision` y `motivo`; el tenant sale de la sesión, nunca del cuerpo.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

function headersFor(locals, withContentType = false) {
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${locals.accessToken}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;
  if (withContentType) headers["Content-Type"] = "application/json";
  return headers;
}

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  try {
    const res = await fetch(`${API_V1}/config/asistente`, { headers: headersFor(locals) });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de parámetros." },
      { status: 503 },
    );
  }
}

/** @type {import('./$types').RequestHandler} */
export async function PUT({ locals, request }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  if (typeof body?.habilitado !== "boolean") {
    return json(
      { error: "validation_error", detail: "El campo 'habilitado' debe ser verdadero o falso." },
      { status: 400 },
    );
  }

  // Allowlist: `habilitado` + (al encender) `declaracion_transmision` booleana y `motivo`.
  /** @type {{habilitado: boolean, declaracion_transmision?: boolean, motivo?: string}} */
  const payload = { habilitado: body.habilitado };
  if (typeof body.declaracion_transmision === "boolean") {
    payload.declaracion_transmision = body.declaracion_transmision;
  }
  if (typeof body.motivo === "string" && body.motivo.trim()) {
    payload.motivo = body.motivo.trim().slice(0, 1000);
  }

  try {
    const res = await fetch(`${API_V1}/config/asistente`, {
      method: "PUT",
      headers: headersFor(locals, true),
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo actualizar la configuración." },
      { status: 503 },
    );
  }
}
