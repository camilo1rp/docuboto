// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /desclasificaciones/api/[id]/aprobar — Aprobar (y ejecutar) la desclasificación
 * de un radicado clasificado (C-2, ADR-039, D-05). `[id]` es el de la SOLICITUD.
 *
 * POST → POST /api/v1/desclasificaciones/{id}/aprobar
 *   Sin cuerpo: el backend no admite nada más que la decisión. Aprobar baja el
 *   nivel del radicado en la misma transacción y la información deja de estar
 *   protegida: es irreversible en la práctica (se difunde). La confirmación
 *   escalonada vive en la pantalla; aquí solo se exige que el cliente declare
 *   `confirmado: true` para que un POST suelto no ejecute el acto.
 *
 * El backend re-valida PERM_RECLASIFICAR crud>=4, clearance (en el WHERE) y que
 * quien aprueba NO sea quien solicitó (409 `separacion_de_funciones`).
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
  if (!params.id || !UUID_RE.test(params.id)) {
    return json(
      { error: "validation_error", detail: "Identificador de solicitud inválido." },
      { status: 400 },
    );
  }

  /** @type {any} */
  let body = null;
  try {
    body = await request.json();
  } catch {
    body = null;
  }
  if (body?.confirmado !== true) {
    return json(
      {
        error: "confirmacion_requerida",
        detail: "Aprobar es irreversible: confirme la aprobación expresamente.",
      },
      { status: 422 },
    );
  }

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": locals.user?.tenant_slug ?? "",
  };
  if (locals.user?.sub) headers["X-User-Id"] = locals.user.sub;

  try {
    const res = await fetch(
      `${API_V1}/desclasificaciones/${encodeURIComponent(params.id)}/aprobar`,
      {
        method: "POST",
        headers,
      },
    );
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo aprobar la desclasificación." },
      { status: 503 },
    );
  }
}
