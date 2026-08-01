// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/pinar/api/planes/[id]/aprobar — Proxy SSR: aprueba un plan PINAR en
 * 'borrador' (D-05, RF-ADM-08, Ac. AGN 003/2015, Fase 7).
 *
 * POST → borrador → aprobado. USUA_PERM_ADMIN en el backend
 *   (services/tenant-service/app/routers/pinar.py:166-183). El acto es
 *   imputable e IRREVERSIBLE: a partir de 'aprobado' el contenido estructural
 *   del plan queda congelado (`_EDITABLE = {"borrador"}` en pinar_service.py)
 *   y el asiento queda en `audit_log` inmutable. La UI advierte esto ANTES de
 *   confirmar (ver Modal en admin/pinar/[id]/+page.svelte), no después.
 *
 * Body requerido: { acto_administrativo: string, 1..200 }. Sin X-User-Id
 * válido el backend responde 400 "actor_requerido" (fail-closed).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params, request }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  const { id } = params;
  if (!id || !UUID_RE.test(id)) {
    return json({ error: "validation_error", detail: "Identificador de plan inválido." }, { status: 400 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const { acto_administrativo } = body ?? {};
  if (typeof acto_administrativo !== "string" || !acto_administrativo.trim() || acto_administrativo.length > 200) {
    return json(
      { error: "validation_error", detail: "El acto administrativo es obligatorio (máx. 200 caracteres)." },
      { status: 400 },
    );
  }

  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${locals.accessToken}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/pinar/planes/${encodeURIComponent(id)}/aprobar`, {
      method: "POST",
      headers,
      body: JSON.stringify({ acto_administrativo: acto_administrativo.trim() }),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json({ error: "connection_error", detail: "No se pudo aprobar el plan PINAR." }, { status: 503 });
  }
}
