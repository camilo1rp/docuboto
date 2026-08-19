// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /envios/api/[id]/estado — Proxy SSR para cambiar el estado de un envío (D-05, T-12e).
 *
 * POST { estado, descripcion?, motivo? }
 *   → POST /api/v1/envios/{id}/estado
 *   → ShipmentResponse
 *
 * Allowlist explícita (ShipmentStatusUpdate).
 * Para la devolución con causal, `descripcion` lleva el prefijo
 * "Causal: <causal> — <nota>" formateado en el cliente, y `motivo` lleva la
 * causal SOLA, para que quede en `motivo_devolucion` y no únicamente dentro de
 * un texto libre. Este comentario decía que la causal estructurada era
 * «roadmap»: el campo existía desde el webhook del operador postal, que sí lo
 * alimentaba — lo que faltaba era pasarlo por el camino manual, que es el que
 * usan las personas.
 *
 * X-User-Id: actor traceability (D-05).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const ESTADOS_VALIDOS = new Set(["registrado", "en_transito", "entregado", "devuelto", "fallido"]);

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

  // Allowlist explícita (ShipmentStatusUpdate).
  const { estado, descripcion, motivo } = body ?? {};

  if (!estado || !ESTADOS_VALIDOS.has(estado)) {
    return json(
      {
        error: "validation_error",
        detail: "Estado no válido. Use: registrado, en_transito, entregado, devuelto o fallido.",
      },
      { status: 422 },
    );
  }

  const payload = {
    estado,
    descripcion: typeof descripcion === "string" && descripcion.trim() ? descripcion.trim() : null,
    motivo: typeof motivo === "string" && motivo.trim() ? motivo.trim() : null,
  };

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/envios/${encodeURIComponent(params.id)}/estado`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });

    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de envíos." },
      { status: 503 },
    );
  }
}
