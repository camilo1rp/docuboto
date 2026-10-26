// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /borradores/api/metadata-template — Endpoint de servidor para plantillas de metadatos (D-05, ADR-011).
 *
 * El token se lee de la cookie httpOnly del servidor y nunca se expone al JS del cliente.
 * Se usa en el modal de radicar un borrador al elegir el tipo documental.
 *
 * GET → lista plantillas de metadatos del tipo documental seleccionado.
 *   Parámetros: tipo_documental (string, código del tipo documental; obligatorio:
 *   sin él → 400, para no listar las plantillas de todos los tipos).
 *   Respuesta: plantilla[] (`MetadataTemplateResponse`; el schema va en `json_schema`)
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";

  const tipoDocumental = (url.searchParams.get("tipo_documental") ?? "").trim();
  if (!tipoDocumental) {
    return json(
      { error: "invalid_query", detail: "Indique el tipo documental.", status: 400 },
      { status: 400 },
    );
  }
  const params = new URLSearchParams({ tipo_documental: tipoDocumental });

  const queryString = params.toString() ? `?${params}` : "";

  try {
    const res = await fetch(`${API_V1}/metadata/templates${queryString}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });

    const data = await res.json().catch(() => []);
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo cargar las plantillas de metadatos." },
      { status: 503 },
    );
  }
}
