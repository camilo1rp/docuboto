// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /bandeja/api/[id]/transactions — Proxy SSR: ejecuta una transacción de trámite
 * sobre un radicado (D-05, ADR-011, E05 §Fase 1, RF-FLU-01).
 *
 * El permiso NO es fijo en este endpoint: el backend exige, por-tipo, el permiso
 * declarado en `transaction_types.requires_permission` (p. ej. `anular`→PERM_ANULAR),
 * resuelto en runtime en el servicio — el proxy no puede ni debe adelantarlo, solo
 * reenvía y deja que el backend devuelva el 403 si corresponde.
 *
 * El token y el userId se leen de event.locals. El JS del cliente nunca los ve.
 *
 * POST → ejecuta la transacción (`POST /workflows/{radicado_id}/transactions`)
 *   Body (TransactionRequest, allowlist): {
 *     tipo_tx (str ≤40, requerido), comentario (str, opcional), detalles (object, opcional)
 *   }
 *   Respuesta: FlowEventResponse o { error, detail, status } (404 unknown_transaction/no_flow,
 *   400 para el resto de errores de negocio — se reenvían tal cual).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

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

  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const { tipo_tx, comentario, detalles } = body ?? {};

  if (!tipo_tx || typeof tipo_tx !== "string" || !tipo_tx.trim()) {
    return json({ error: "validation_error", detail: "tipo_tx es obligatorio." }, { status: 422 });
  }
  if (tipo_tx.trim().length > 40) {
    return json(
      { error: "validation_error", detail: "tipo_tx no puede superar 40 caracteres." },
      { status: 422 },
    );
  }
  if (
    detalles !== undefined &&
    detalles !== null &&
    (typeof detalles !== "object" || Array.isArray(detalles))
  ) {
    return json(
      { error: "validation_error", detail: "detalles debe ser un objeto." },
      { status: 422 },
    );
  }

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  // Allowlist campo a campo — nunca se reenvía el body íntegro del cliente.
  /** @type {Record<string, unknown>} */
  const payload = { tipo_tx: tipo_tx.trim() };
  if (typeof comentario === "string" && comentario.trim()) {
    payload.comentario = comentario.trim().slice(0, 2000);
  }
  if (detalles && typeof detalles === "object" && !Array.isArray(detalles)) {
    payload.detalles = detalles;
  }

  try {
    const res = await fetch(`${API_V1}/workflows/${encodeURIComponent(id)}/transactions`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });

    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo ejecutar la transacción de trámite." },
      { status: 503 },
    );
  }
}
