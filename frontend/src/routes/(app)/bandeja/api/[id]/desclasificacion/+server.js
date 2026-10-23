// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /bandeja/api/[id]/desclasificacion — Proxy SSR de la desclasificación de un
 * radicado CLASIFICADO (nivel 3) con segunda persona (C-2, ADR-039, D-05).
 *
 * GET  → GET /api/v1/documents/{id}/desclasificacion
 *        Historial de solicitudes del radicado (lista; `estado_efectivo` ya
 *        distingue una pendiente vigente de una caducada). Vacío si no hay
 *        ninguna O si el radicado supera el clearance (el backend no distingue).
 * POST → POST /api/v1/documents/{id}/desclasificacion
 *        Body: { nivel_seguridad: 1|2, motivo (10..500), [causal_reserva],
 *        [fundamento_juridico], [plazo_reserva_meses] }
 *
 * Allowlist campo a campo (anti mass-assignment). El backend re-valida RBAC
 * (PERM_RECLASIFICAR crud>=3 para solicitar, >=1 para el historial), clearance
 * (en el WHERE) y las reglas de la causal; el 404 es neutro y se conserva así.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { codigoError, mensajeError } from "$lib/utils/apiError.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @param {import('@sveltejs/kit').RequestEvent} event */
function cabeceras({ locals }) {
  /** @type {Record<string, string>} */
  const h = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${locals.accessToken ?? ""}`,
    "X-Tenant-Slug": locals.user?.tenant_slug ?? "",
  };
  if (locals.user?.sub) h["X-User-Id"] = locals.user.sub;
  return h;
}

/** @type {import('./$types').RequestHandler} */
export async function GET(event) {
  const { locals, params } = event;
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  if (!params.id || !UUID_RE.test(params.id)) {
    return json(
      { error: "validation_error", detail: "Identificador de radicado inválido." },
      { status: 400 },
    );
  }
  try {
    const res = await fetch(
      `${API_V1}/documents/${encodeURIComponent(params.id)}/desclasificacion`,
      {
        headers: cabeceras(event),
      },
    );
    const cuerpo = await res.json().catch(() => null);
    if (!res.ok) {
      return json(
        {
          error: codigoError(cuerpo) ?? "upstream_error",
          detail: mensajeError(cuerpo, `Error ${res.status}`),
        },
        { status: res.status },
      );
    }
    return json(Array.isArray(cuerpo) ? cuerpo : []);
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo consultar el servidor de documentos." },
      { status: 503 },
    );
  }
}

/** @type {import('./$types').RequestHandler} */
export async function POST(event) {
  const { locals, params, request } = event;
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  if (!params.id || !UUID_RE.test(params.id)) {
    return json(
      { error: "validation_error", detail: "Identificador de radicado inválido." },
      { status: 400 },
    );
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const { nivel_seguridad, motivo, causal_reserva, fundamento_juridico, plazo_reserva_meses } =
    body ?? {};

  const nivel = Number(nivel_seguridad);
  if (nivel !== 1 && nivel !== 2) {
    return json(
      {
        error: "validation_error",
        detail: "El nivel de destino de una desclasificación es 1 (Pública) o 2 (Reservada).",
      },
      { status: 422 },
    );
  }
  const motivoLimpio = typeof motivo === "string" ? motivo.trim() : "";
  if (motivoLimpio.length < 10 || motivoLimpio.length > 500) {
    return json(
      {
        error: "validation_error",
        detail: "El motivo debe tener entre 10 y 500 caracteres.",
      },
      { status: 422 },
    );
  }

  /** @type {Record<string, unknown>} */
  const payload = { nivel_seguridad: nivel, motivo: motivoLimpio };
  if (typeof causal_reserva === "string" && causal_reserva.trim()) {
    payload.causal_reserva = causal_reserva.trim().slice(0, 100);
  }
  if (typeof fundamento_juridico === "string" && fundamento_juridico.trim()) {
    payload.fundamento_juridico = fundamento_juridico.trim().slice(0, 300);
  }
  if (
    plazo_reserva_meses !== undefined &&
    plazo_reserva_meses !== null &&
    plazo_reserva_meses !== ""
  ) {
    const plazo = Number(plazo_reserva_meses);
    if (!Number.isInteger(plazo) || plazo < 1 || plazo > 180) {
      return json(
        {
          error: "validation_error",
          detail: "El plazo de reserva debe ser un número entero de 1 a 180 meses.",
        },
        { status: 422 },
      );
    }
    payload.plazo_reserva_meses = plazo;
  }

  try {
    const res = await fetch(
      `${API_V1}/documents/${encodeURIComponent(params.id)}/desclasificacion`,
      {
        method: "POST",
        headers: cabeceras(event),
        body: JSON.stringify(payload),
      },
    );
    const data = await res.json().catch(() => ({}));
    if (res.status === 404) {
      return json(
        {
          error: "not_found",
          detail: "No fue posible solicitar la desclasificación o no tiene acceso al radicado.",
        },
        { status: 404 },
      );
    }
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de documentos." },
      { status: 503 },
    );
  }
}
