// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/flujos/reasignacion/api/preview — Estimación previa de cuántos
 * pasos se verían afectados por una reasignación en cascada (E05).
 *
 * `workflow-service` NO ofrece un endpoint de simulación para
 * `POST /workflows/reassign/cascade` — se buscó explícitamente y no existe
 * (ver `app/routers/workflow.py`/`app/services/workflow_service.py`). Este
 * proxy NO inventa un endpoint nuevo: reutiliza `GET /workflows/pending`
 * (bandeja), cuyo `WHERE status IN ('pending','in_progress') AND
 * (assigned_to=$1 | to_dept=$1)` es —campo a campo— EL MISMO predicado que
 * usa `reassign_cascade` en `workflow_repository.py`. Se pide `size=1` (no
 * interesan las filas, solo el total) y se lee `X-Total-Count`.
 *
 * ES UNA ESTIMACIÓN, no el conteo exacto que se reasignará:
 *   - `list_pending` filtra ADEMÁS por el clearance del llamante
 *     (`_clearance_filter`, RF-SEG-08 no-read-up) — un radicado clasificado
 *     por encima del clearance de quien preview-ea no se cuenta aquí, pero
 *     SÍ se reasignaría en la ejecución real (`reassign_cascade` no filtra
 *     por clearance, es un acto de tenencia). El conteo de la vista previa
 *     puede entonces ser MENOR o igual al real — nunca mayor.
 *   - Ventana de carrera: entre la vista previa y la confirmación pueden
 *     entrar/salir pasos pendientes.
 * El `+page.svelte` etiqueta el número como "estimado", nunca como el
 * resultado exacto.
 *
 * GET ?assigned_to=<uuid> | ?dept=<string>  (exactamente uno de los dos)
 *   → { count: number, estimated: true }
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  const assignedTo = url.searchParams.get("assigned_to");
  const dept = url.searchParams.get("dept");

  if ((!assignedTo && !dept) || (assignedTo && dept)) {
    return json(
      {
        error: "validation_error",
        detail: "Indique exactamente un origen: usuario o dependencia.",
      },
      { status: 400 },
    );
  }
  if (assignedTo && !UUID_RE.test(assignedTo)) {
    return json(
      { error: "validation_error", detail: "Identificador de usuario de origen inválido." },
      { status: 400 },
    );
  }

  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  const params = new URLSearchParams({ size: "1" });
  if (assignedTo) params.set("assigned_to", assignedTo);
  if (dept) params.set("dept", dept);

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/workflows/pending?${params}`, { headers });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      return json(
        {
          error: err.error ?? "upstream_error",
          detail: err.detail ?? "No se pudo estimar el impacto.",
        },
        { status: res.status },
      );
    }
    const total = Number(res.headers.get("X-Total-Count") ?? "0");
    return json({ count: Number.isFinite(total) ? total : 0, estimated: true }, { status: 200 });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo estimar el impacto." },
      { status: 503 },
    );
  }
}
