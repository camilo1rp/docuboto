// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /bandeja/api/[id]/respuestas — Proxy SSR: Salidas que responden a este radicado
 * (D-05, ADR-011, F6 frontend). Alimenta la sección "Respuestas" del drawer de
 * detalle del radicado (bandeja/+page.svelte).
 *
 * El token se lee de la cookie httpOnly del servidor y nunca se expone al JS del
 * cliente. Gemelo de lectura de `./respuesta/+server.js` (POST, crea la Salida);
 * este es GET (lista las ya creadas).
 *
 * GET → RespuestaItem[] (document-service, no-read-up RF-SEG-08 ya aplicado por
 *   el backend con el clearance del llamante, ver
 *   services/document-service/app/routers/respuesta.py:97).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  const { id } = params;

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
    "X-Canal": "ui",
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/documents/${encodeURIComponent(id)}/respuestas`, {
      headers,
    });
    const data = await res.json().catch(() => []);
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo cargar las respuestas del radicado." },
      { status: 503 },
    );
  }
}
