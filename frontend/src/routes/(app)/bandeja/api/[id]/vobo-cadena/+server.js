// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /bandeja/api/[id]/vobo-cadena — Proxy SSR: crear cadena de Visto Bueno (D-05, T-08c).
 *
 * Crea la secuencia ordenada de revisores sobre un radicado. El token y el
 * actor se leen de event.locals (cookie httpOnly + claims del JWT). El JS del
 * cliente NUNCA ve el token. El gateway re-deriva X-User-Id y X-Tenant-Slug del
 * JWT firmado — no son falsificables desde el cliente.
 *
 * Autorización: la compuerta autoritativa es el backend + gateway. El backend
 * re-valida RBAC (D-09: crear_cadena exige permiso de gestión de radicados).
 * La UI ya filtra el botón por $permissions; la UI no es fuente de verdad.
 *
 * POST → /api/v1/workflows/{id}/vistos-buenos
 *   Body (CadenaCreate): { revisores: string[] }  (UUIDs, 1..20, sin duplicados)
 *   Respuesta: CadenaResponse o { error, detail, status }.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  const { id } = params;

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  // Validación: revisores debe ser una lista de 1..20 UUIDs sin duplicados.
  const revisores = Array.isArray(body?.revisores) ? body.revisores : null;
  if (!revisores || revisores.length === 0) {
    return json(
      { error: "validation_error", detail: "Debe seleccionar al menos un revisor." },
      { status: 422 },
    );
  }
  if (revisores.length > 20) {
    return json(
      { error: "validation_error", detail: "Máximo 20 revisores en la cadena." },
      { status: 422 },
    );
  }
  const allStrings = revisores.every((r) => typeof r === "string" && r.trim());
  if (!allStrings) {
    return json(
      { error: "validation_error", detail: "Identificadores de revisor inválidos." },
      { status: 422 },
    );
  }
  if (new Set(revisores).size !== revisores.length) {
    return json(
      { error: "validation_error", detail: "Hay revisores duplicados en la cadena." },
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
    const res = await fetch(`${API_V1}/workflows/${id}/vistos-buenos`, {
      method: "POST",
      headers,
      body: JSON.stringify({ revisores }),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo crear la cadena de visto bueno." },
      { status: 503 },
    );
  }
}
