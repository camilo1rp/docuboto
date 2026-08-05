// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /radicar/api/documents — Endpoint de servidor para creación de radicados (D-05, ADR-011).
 *
 * El token se lee de la cookie httpOnly del servidor y nunca se expone al JS del cliente.
 *
 * POST → crea un nuevo radicado (confirmación atómica del stepper T-09).
 *   Body: DocumentCreate (JSON)
 *   Respuesta: DocumentResponse con tracking_number asignado
 *
 * Seguridad (T-12b): para Salida/Interno el campo `observations` es el cuerpo rico del
 * editor TipTap (HTML). El HTML del cliente NO es de fiar (D-05) → se SANEA server-side
 * con la allowlist antes de enviarlo al backend. Para Entrada es texto plano (notas) y
 * se deja tal cual.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { sanitizeHtml } from "$lib/server/sanitizeHtml.js";

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  // T-12b: el cuerpo rico (Salida/Interno) llega como HTML del editor → sanear.
  if (
    body &&
    (body.doc_type === "S" || body.doc_type === "I") &&
    typeof body.observations === "string"
  ) {
    const clean = sanitizeHtml(body.observations);
    body.observations = clean || null;
  }

  try {
    const res = await fetch(`${API_V1}/documents`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
      body: JSON.stringify(body),
    });

    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de documentos." },
      { status: 503 },
    );
  }
}
