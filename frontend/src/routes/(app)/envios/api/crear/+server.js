// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /envios/api/crear — Proxy SSR para crear un nuevo envío postal (D-05, T-12e).
 *
 * POST { radicado_id, operador, destinatario?, direccion? }
 *   → POST /api/v1/documents/{radicado_id}/envios
 *   → ShipmentResponse (201)
 *
 * Allowlist explícita: solo se reenvían los campos del contrato ShipmentCreate.
 * `radicado_id` va en la ruta del gateway (no en el body); el cuerpo solo lleva
 * operador, destinatario y direccion.
 *
 * X-User-Id: actor traceability (D-05, consistente con aprobar/radicar/transferir).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const OPERADORES_VALIDOS = new Set(["4-72", "servientrega", "otro"]);

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, request }) {
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

  // Allowlist explícita (ShipmentCreate).
  const { radicado_id, operador, destinatario, direccion } = body ?? {};

  if (!radicado_id || typeof radicado_id !== "string" || !radicado_id.trim()) {
    return json(
      { error: "validation_error", detail: "El campo radicado_id es obligatorio." },
      { status: 422 },
    );
  }

  if (!operador || !OPERADORES_VALIDOS.has(operador)) {
    return json(
      { error: "validation_error", detail: "Operador no válido. Use: 4-72, servientrega u otro." },
      { status: 422 },
    );
  }

  // Payload para el gateway — solo operador, destinatario y direccion (radicado_id va en la ruta).
  const payload = {
    operador,
    destinatario:
      typeof destinatario === "string" && destinatario.trim() ? destinatario.trim() : null,
    direccion: typeof direccion === "string" && direccion.trim() ? direccion.trim() : null,
  };

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(
      `${API_V1}/documents/${encodeURIComponent(radicado_id.trim())}/envios`,
      {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
      },
    );

    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de envíos." },
      { status: 503 },
    );
  }
}
