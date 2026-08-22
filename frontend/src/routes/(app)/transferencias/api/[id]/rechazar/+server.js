// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /transferencias/api/[id]/rechazar — Proxy SSR rechazo de transferencia (D-05, E12).
 *
 * POST → inyecta X-User-Id, body observacion (opt),
 *         reenvía a POST /transferencias/{id}/rechazar
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

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
    body = {};
  }

  // El backend exige `motivo` (TransferenciaRechazo, min 3, obligatorio) y este
  // proxy mandaba `observacion`, opcional. El desfase de nombre dejaba la rama
  // ENTERA muerta: todo rechazo devolvía 422 y ninguno llegó nunca a ocurrir.
  //
  // El nombre no es un detalle: `motivo` es obligatorio a propósito, porque el
  // rechazo es un estado TERMINAL y su porqué es el insumo de la subsanación
  // para la oficina de origen. Un rechazo inmotivado deja a quien transfiere
  // sin saber qué corregir.
  const motivo = typeof body?.motivo === "string" ? body.motivo.trim() : "";
  if (motivo.length < 3) {
    return json(
      {
        error: "validation_error",
        detail: "Indique el motivo del rechazo (mínimo 3 caracteres).",
      },
      { status: 400 },
    );
  }

  /** @type {Record<string, unknown>} */
  const payload = { motivo: motivo.slice(0, 2000) };

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/transferencias/${id}/rechazar`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
    if (res.status === 204) {
      return json({ ok: true }, { status: 200 });
    }
    const data = await res.json().catch(() => ({ ok: true }));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo rechazar la transferencia." },
      { status: 503 },
    );
  }
}
