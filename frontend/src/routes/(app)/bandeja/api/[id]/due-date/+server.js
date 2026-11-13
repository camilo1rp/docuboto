// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /bandeja/api/[id]/due-date — Proxy SSR para corregir el vencimiento.
 *
 * POST { motivo, due_date | business_days }
 *   → PUT /api/v1/documents/{id}/due-date
 *
 * Allowlist: fecha o días, y el motivo. `origen` no se reenvía: la ruta
 * pública es siempre manual.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { codigoError, mensajeError } from "$lib/utils/apiError.js";

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params, request }) {
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

  const motivo = typeof body?.motivo === "string" ? body.motivo.trim() : "";
  if (motivo.length < 10 || motivo.length > 500) {
    return json(
      {
        error: "validation_error",
        detail: "El motivo debe tener entre 10 y 500 caracteres.",
      },
      { status: 422 },
    );
  }

  const fecha = typeof body?.due_date === "string" ? body.due_date.trim() : "";
  const diasCrudo = body?.business_days;
  const tieneDias = diasCrudo !== undefined && diasCrudo !== null && diasCrudo !== "";
  if ((fecha !== "") === tieneDias) {
    return json(
      {
        error: "validation_error",
        detail: "Indique la fecha o los días hábiles.",
      },
      { status: 422 },
    );
  }

  /** @type {Record<string, unknown>} */
  const payload = { motivo };
  if (fecha) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(fecha)) {
      return json(
        { error: "validation_error", detail: "La fecha debe tener la forma AAAA-MM-DD." },
        { status: 422 },
      );
    }
    payload.due_date = fecha;
  } else {
    const dias = Number(diasCrudo);
    if (!Number.isInteger(dias) || dias < 1 || dias > 3650) {
      return json(
        {
          error: "validation_error",
          detail: "Los días hábiles deben ser un entero de 1 a 3650.",
        },
        { status: 422 },
      );
    }
    payload.business_days = dias;
  }

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/documents/${encodeURIComponent(params.id)}/due-date`, {
      method: "PUT",
      headers,
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 404) {
      return json(
        {
          error: "not_found",
          detail: "No fue posible corregir el vencimiento o no tiene acceso al radicado.",
        },
        { status: 404 },
      );
    }
    if (!res.ok) {
      return json(
        {
          error: codigoError(data) ?? "error",
          detail: mensajeError(data, "No se pudo corregir el vencimiento."),
        },
        { status: res.status },
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
