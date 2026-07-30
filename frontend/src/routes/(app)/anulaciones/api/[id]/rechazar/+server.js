// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /anulaciones/api/[id]/rechazar — Proxy SSR: rechazar la anulación de un
 * radicado (paso 2, D-05, ADR-011, Fase 0 cierre desfase API↔UI).
 *
 * El token y el userId se leen de event.locals. El JS del cliente nunca los
 * ve. El backend re-valida PERM_PANU_CODI + clearance (no-read-up, RF-SEG-08).
 *
 * El motivo es OBLIGATORIO en este proxy aunque el schema del backend
 * (`AnulacionDecision.observacion`) lo declare opcional — mismo criterio que
 * el proxy hermano `./aprobar`: rechazar es un acto con traza de auditoría
 * y el solicitante necesita saber por qué. Se envía como `observacion`.
 *
 * M9 (revisión UX Fase 0): un motivo que excede 2000 caracteres se
 * RECHAZA (422) — antes se recortaba en silencio con `.slice(0, 2000)`.
 * Ver la nota completa en el proxy hermano `./aprobar`.
 *
 * POST → rechaza la anulación (la solicitud queda `rechazada`; el radicado
 *   NO cambia de estado).
 *   Body: { motivo: string (1..2000) }
 *   Respuesta: AnulacionResponse o { error, detail, status }
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

  const { motivo } = body ?? {};

  // Motivo obligatorio: acto con efectos jurídicos y traza de auditoría.
  if (!motivo || typeof motivo !== "string" || !motivo.trim()) {
    return json(
      { error: "validation_error", detail: "El motivo del rechazo es obligatorio." },
      { status: 422 },
    );
  }

  // M9: rechazar en vez de recortar en silencio (ver nota de cabecera).
  if (motivo.trim().length > 2000) {
    return json(
      {
        error: "validation_error",
        detail: "El motivo del rechazo no puede superar 2000 caracteres.",
      },
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

  // Allowlist: solo `observacion` (campo que espera el backend) — nunca se
  // reenvía el body íntegro del cliente (anti mass-assignment). El límite ya
  // se validó arriba (422 si lo excede) — aquí no se recorta.
  const payload = { observacion: motivo.trim() };

  try {
    const res = await fetch(`${API_V1}/documents/${encodeURIComponent(id)}/anulacion/rechazar`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });

    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo rechazar la anulación." },
      { status: 503 },
    );
  }
}
