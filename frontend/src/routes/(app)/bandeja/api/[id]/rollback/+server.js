// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /bandeja/api/[id]/rollback — Proxy SSR: revierte la última asignación de un
 * radicado (D-05, ADR-011, E05 §Fase 1, RF-FLU-08).
 *
 * El backend (`RollbackRequest.motivo` en workflow-service) ahora EXIGE un
 * motivo de 5 a 500 caracteres, que queda asentado de forma atómica en
 * `flow_events.comentario` y en `public.audit_log` junto con la mutación —
 * reversión sin motivo trazable en un acto administrativo. Este proxy valida
 * los mismos límites ANTES de tocar el gateway (evita un viaje 422 al
 * backend) y reenvía solo `motivo` (allowlist campo a campo, anti
 * mass-assignment — nunca el body íntegro del cliente).
 *
 * `RollbackError` del servicio se mapea a 409 por el backend; se reenvía tal
 * cual (no se traduce a 500).
 *
 * El token y el userId se leen de event.locals. El JS del cliente nunca los ve.
 *
 * POST → revierte el último paso (`POST /workflows/{radicado_id}/rollback`)
 *   Body: { motivo: string (5..500) }
 *   Respuesta: FlowStepResponse o { error, detail, status } (409 si no se puede revertir)
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MOTIVO_MIN_LENGTH = 5;
const MOTIVO_MAX_LENGTH = 500;

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  const { id } = params;
  if (!id || !UUID_RE.test(id)) {
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

  const { motivo } = body ?? {};
  const motivoTrimmed = typeof motivo === "string" ? motivo.trim() : "";

  if (motivoTrimmed.length < MOTIVO_MIN_LENGTH) {
    return json(
      {
        error: "validation_error",
        detail: `El motivo de la reversión debe tener al menos ${MOTIVO_MIN_LENGTH} caracteres.`,
      },
      { status: 422 },
    );
  }
  if (motivoTrimmed.length > MOTIVO_MAX_LENGTH) {
    return json(
      {
        error: "validation_error",
        detail: `El motivo de la reversión no puede superar ${MOTIVO_MAX_LENGTH} caracteres.`,
      },
      { status: 422 },
    );
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

  // Allowlist: solo `motivo` — nunca se reenvía el body íntegro del cliente
  // (anti mass-assignment). El límite ya se validó arriba.
  const payload = { motivo: motivoTrimmed };

  try {
    const res = await fetch(`${API_V1}/workflows/${encodeURIComponent(id)}/rollback`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });

    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo revertir la asignación." },
      { status: 503 },
    );
  }
}
