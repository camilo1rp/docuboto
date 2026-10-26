// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /bandeja/api/[id]/disposition — Proxy SSR de la disposición final (TRD) del
 * radicado (RF-MET-08, D-05, Fase 4 cierre desfase API↔UI).
 *
 * Endpoints reales (services/document-service/app/routers/documents.py:374,
 * :402), ambos gateados por `USUA_PERM_EXPEDIENTE`:
 *   GET   /api/v1/documents/{id}/disposition   → { radicado_id, disposition }
 *   PATCH /api/v1/documents/{id}/disposition   → { radicado_id, disposition }
 *
 * No-read-up (RF-SEG-08): un radicado inexistente y uno por encima del
 * clearance del llamante devuelven el MISMO 404 neutro — este proxy preserva
 * esa opacidad (mismo patrón que security-level/+server.js) en vez de
 * inventar un mensaje que distinga los dos casos.
 *
 * Allowlist campo a campo del `DispositionUpdate` real (document.py:125):
 * `programa`, `retention_until`, `accion` ("conservar"|"eliminar"|
 * "transferir"|"seleccionar"), `confirmado`, `marcado_eliminacion`.
 *
 * IMPORTANTE (verificado contra el backend): un radicado individual NO tiene
 * un vínculo directo a una serie TRD (`doc_class` es texto libre, sin FK). El
 * único vínculo documento↔TRD vive a nivel de EXPEDIENTE en archive-service
 * (`expediente.trd_serie_id`), y no existe un endpoint inverso "expedientes
 * que contienen este radicado". Por eso este proxy no intenta resolver una
 * regla de retención automática para el radicado — el cliente lo declara
 * explícitamente en la interfaz en vez de inventar el vínculo.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const ACCIONES_VALIDAS = new Set(["conservar", "eliminar", "transferir", "seleccionar"]);
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function opaque404() {
  return json(
    {
      error: "not_found",
      detail: "No fue posible encontrar el radicado o no tiene acceso a él.",
    },
    { status: 404 },
  );
}

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/documents/${encodeURIComponent(params.id)}/disposition`, {
      headers,
    });
    if (res.status === 404) return opaque404();
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de documentos." },
      { status: 503 },
    );
  }
}

/** @type {import('./$types').RequestHandler} */
export async function PATCH({ locals, params, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const {
    programa,
    retention_until: retentionUntil,
    accion,
    confirmado,
    marcado_eliminacion: marcadoEliminacion,
  } = body ?? {};

  /** @type {Record<string, unknown>} */
  const payload = {};

  if (programa !== undefined) {
    if (programa !== null && (typeof programa !== "string" || programa.length > 200)) {
      return json(
        {
          error: "validation_error",
          detail: "El programa de retención no es válido (máx. 200 caracteres).",
        },
        { status: 422 },
      );
    }
    payload.programa = typeof programa === "string" ? programa.trim() || null : null;
  }

  if (retentionUntil !== undefined) {
    if (retentionUntil !== null && !DATE_RE.test(String(retentionUntil))) {
      return json(
        {
          error: "validation_error",
          detail: "La fecha de disposición debe tener formato YYYY-MM-DD.",
        },
        { status: 422 },
      );
    }
    payload.retention_until = retentionUntil;
  }

  if (accion !== undefined) {
    if (accion !== null && !ACCIONES_VALIDAS.has(accion)) {
      return json(
        {
          error: "validation_error",
          detail: "La acción debe ser conservar, eliminar, transferir o seleccionar.",
        },
        { status: 422 },
      );
    }
    payload.accion = accion;
  }

  if (confirmado !== undefined) {
    payload.confirmado = Boolean(confirmado);
  }

  if (marcadoEliminacion !== undefined) {
    payload.marcado_eliminacion = Boolean(marcadoEliminacion);
  }

  if (Object.keys(payload).length === 0) {
    return json(
      { error: "validation_error", detail: "No hay cambios de disposición para guardar." },
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

  try {
    const res = await fetch(`${API_V1}/documents/${encodeURIComponent(params.id)}/disposition`, {
      method: "PATCH",
      headers,
      body: JSON.stringify(payload),
    });
    if (res.status === 404) return opaque404();
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de documentos." },
      { status: 503 },
    );
  }
}
