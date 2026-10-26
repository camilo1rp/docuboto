// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /bandeja/api/transaction-types — Proxy SSR: catálogo de tipos de trámite
 * (D-05, ADR-011, E05 §Fase 1, RF-FLU-01).
 *
 * El token se lee de la cookie httpOnly del servidor y nunca se expone al JS del
 * cliente. Endpoint de solo lectura sin parámetros — el backend solo exige tenant.
 *
 * GET → catálogo completo (`GET /workflows/transaction-types`)
 *   Respuesta: TransactionTypeResponse[] (se reenvía tal cual, sin envoltorio:
 *   no es una lista paginada, es un catálogo).
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

  try {
    const res = await fetch(`${API_V1}/workflows/transaction-types`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });

    const data = await res.json().catch(() => []);
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de flujos." },
      { status: 503 },
    );
  }
}
