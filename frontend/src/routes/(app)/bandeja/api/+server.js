// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /bandeja/api — Endpoint de servidor para listar radicados (D-05, ADR-011).
 *
 * El navegador llama a este endpoint local (sin token); el servidor lo lee
 * de la cookie httpOnly y lo propaga al gateway — el access_token nunca
 * alcanza el JS del cliente.
 *
 * GET → lista paginada de radicados
 *   Parámetros: doc_type (E|S|I), status, page, size
 *   Respuesta: { documents: DocumentResponse[], total: number }
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, url }) {
  // event.locals.accessToken ya incorpora el refresh transparente del hook
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";

  const docType = url.searchParams.get("doc_type") ?? "";
  const status = url.searchParams.get("status") ?? "";
  const page = url.searchParams.get("page") ?? "1";
  const size = url.searchParams.get("size") ?? "20";

  const params = new URLSearchParams({ page, size });
  if (docType) params.set("doc_type", docType);
  if (status) params.set("status", status);

  try {
    const res = await fetch(`${API_V1}/documents?${params}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });

    const total = Number(res.headers.get("X-Total-Count") ?? 0);
    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      return json(data, { status: res.status });
    }

    return json({ documents: data, total });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de documentos." },
      { status: 503 },
    );
  }
}
