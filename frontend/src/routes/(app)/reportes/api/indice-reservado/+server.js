// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /reportes/api/indice-reservado — Proxy SSR del índice de información
 * clasificada y reservada (E08, Ley 1712/2014 art. 20; Fase 4 cierre desfase
 * API↔UI, D-05).
 *
 * Endpoint real: GET /api/v1/reports/indice-reservado
 * (services/document-service/app/routers/reports.py:130), gateado por
 * `PERM_RECLASIFICAR` — el mismo permiso que ya gobierna la reclasificación
 * de nivel de seguridad en /bandeja (bandeja/api/[id]/security-level).
 *
 * Registro CONTENT-FREE por diseño (ortogonal al clearance de lectura, ver
 * docstring del router): enumera QUÉ está clasificado/reservado sin revelar
 * el `subject`/contenido protegido. El proxy no añade ni permite añadir
 * ningún campo fuera de lo que el backend ya expone.
 *
 * GET → { total: number, items: IndiceClasificadoItem[] }
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
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
    const res = await fetch(`${API_V1}/reports/indice-reservado`, { headers });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      {
        error: "connection_error",
        detail: "No se pudo conectar al servidor para consultar el índice de clasificados.",
      },
      { status: 503 },
    );
  }
}
