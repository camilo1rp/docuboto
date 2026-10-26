// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /anulaciones/api/[id]/aprobar — Proxy SSR: aprobar la anulación de un
 * radicado (paso 2, D-05, ADR-011, Fase 0 cierre desfase API↔UI).
 *
 * El token y el userId se leen de event.locals. El JS del cliente nunca los
 * ve. El backend re-valida PERM_PANU_CODI + clearance (no-read-up, RF-SEG-08).
 *
 * El motivo es OBLIGATORIO en este proxy aunque el schema del backend
 * (`AnulacionDecision.observacion`) lo declare opcional: aprobar/rechazar
 * una anulación es un acto con efectos jurídicos y traza de auditoría (el
 * radicado pasa a `anulado`, Ley 594/2000 art. 28) — no puede quedar sin
 * motivación registrada. Se envía como `observacion` (nombre del campo que
 * espera el backend).
 *
 * M9 (revisión UX Fase 0): un motivo que excede 2000 caracteres se
 * RECHAZA (422) — antes se recortaba en silencio con `.slice(0, 2000)`,
 * lo que descartaba sin avisar parte de la motivación de un acto con
 * traza de auditoría. El cliente ya limita la escritura a 2000 (contador
 * visible en el formulario); este 422 es la red de seguridad.
 *
 * POST → aprueba la anulación (el radicado pasa a `anulado`).
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
      { error: "validation_error", detail: "El motivo de la aprobación es obligatorio." },
      { status: 422 },
    );
  }

  // M9: rechazar en vez de recortar en silencio (ver nota de cabecera).
  if (motivo.trim().length > 2000) {
    return json(
      {
        error: "validation_error",
        detail: "El motivo de la aprobación no puede superar 2000 caracteres.",
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
    const res = await fetch(`${API_V1}/documents/${encodeURIComponent(id)}/anulacion/aprobar`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });

    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo aprobar la anulación." },
      { status: 503 },
    );
  }
}
