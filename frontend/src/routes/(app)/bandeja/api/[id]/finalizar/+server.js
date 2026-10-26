// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /bandeja/api/[id]/finalizar — Proxy SSR: finalizar el trámite de un radicado
 * (D-05, ADR-011, ADR-028).
 *
 * `POST /workflows/{radicado_id}/finalizar` EXIGE `{ comentario: string (5..500), motivo_cierre: respondido|nrr|
 * traslado_competencia|otro }` (422 si falta) y `PERM_TRAMITAR` nivel 3 (403 con menos)
 * y es el ÚNICO acto que cierra un radicado (`closed`, terminal, irreversible).
 * Este proxy valida los mismos límites antes de llamar al gateway y reenvía solo
 * `comentario` y `motivo_cierre` (allowlist, anti mass-assignment). El backend re-valida RBAC
 * (PERM_TRAMITAR) y tenencia: un no-poseedor recibe `404 no_active_step`; un
 * radicado anulado, `409 radicado_terminal` — se reenvían tal cual.
 *
 * El token se lee de event.locals; el JS del cliente nunca lo ve.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { esMotivoCierre } from "$lib/utils/tenencia.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const COMENTARIO_MIN_LENGTH = 5;
const COMENTARIO_MAX_LENGTH = 500;

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

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const comentario = typeof body?.comentario === "string" ? body.comentario.trim() : "";
  if (comentario.length < COMENTARIO_MIN_LENGTH) {
    return json(
      {
        error: "validation_error",
        detail: `El comentario de cierre debe tener al menos ${COMENTARIO_MIN_LENGTH} caracteres.`,
      },
      { status: 422 },
    );
  }
  if (comentario.length > COMENTARIO_MAX_LENGTH) {
    return json(
      {
        error: "validation_error",
        detail: `El comentario de cierre no puede superar ${COMENTARIO_MAX_LENGTH} caracteres.`,
      },
      { status: 422 },
    );
  }

  const motivoCierre = body?.motivo_cierre;
  if (!esMotivoCierre(motivoCierre)) {
    return json(
      {
        error: "validation_error",
        detail: "Seleccione el motivo de cierre: respondido, nrr, traslado_competencia u otro.",
      },
      { status: 422 },
    );
  }

  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/workflows/${encodeURIComponent(id)}/finalizar`, {
      method: "POST",
      headers,
      body: JSON.stringify({ comentario, motivo_cierre: motivoCierre }),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo finalizar el trámite." },
      { status: 503 },
    );
  }
}
