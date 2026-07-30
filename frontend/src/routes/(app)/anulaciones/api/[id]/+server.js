// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /anulaciones/api/[id] — Proxy SSR: estado de la anulación de un radicado
 * (D-05, ADR-011, Fase 0 cierre desfase API↔UI).
 *
 * El token se lee de event.locals (cookie httpOnly). El JS del cliente
 * NUNCA lo ve. El backend re-valida RBAC y clearance (no-read-up, RF-SEG-08).
 *
 * NOTA sobre este proxy: `GET /documents/{id}/anulacion` no tiene un
 * `require_permission` propio (residual documentado en
 * document-service/CLAUDE.md — "falta decidir cuál exigir sin romper al
 * aprobador"); cualquier usuario autenticado del tenant que conozca el id
 * puede consultar el estado. Este mismo proxy se reutiliza desde
 * `/bandeja` (drawer de detalle, para que el solicitante vea en qué quedó
 * su solicitud) y desde `/anulaciones` (cola del aprobador) — no se duplica.
 *
 * GET → { id, radicado_id, estado, causal, motivo, solicitado_por,
 *         solicitado_at, decidido_por, decidido_at, observacion }
 *   404 si el radicado no existe, supera el clearance del llamante, o nunca
 *   tuvo una solicitud de anulación (mismo 404 no-read-up, indistinguible).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params }) {
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

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/documents/${encodeURIComponent(id)}/anulacion`, {
      headers,
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo consultar el estado de la anulación." },
      { status: 503 },
    );
  }
}
