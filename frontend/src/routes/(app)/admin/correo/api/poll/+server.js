// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/correo/api/poll — Proxy SSR de `POST /api/v1/ingest/email/poll`
 * (document-service, `USUA_PERM_ADMIN`).
 *
 * El acto NO es idempotente y no se deshace: crea un radicado de Entrada por
 * correo —consumiendo números de la secuencia oficial, que es inmutable— y
 * marca los mensajes como leídos en el buzón, con lo que el siguiente poll ya
 * no los verá. De ahí que la pantalla lo pida con confirmación explícita.
 *
 * Respuestas de negocio que el cliente distingue (no son "error genérico"):
 *   - 503 email_not_configured        → no hay cliente IMAP.
 *   - 503 imap_tenant_not_configured  → el buzón no declara entidad dueña.
 *   - 403 imap_tenant_mismatch        → el buzón es de otra entidad.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": locals.user?.tenant_slug ?? "",
  };
  const userId = locals.user?.sub ?? "";
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/ingest/email/poll`, { method: "POST", headers });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servicio de radicación." },
      { status: 503 },
    );
  }
}
