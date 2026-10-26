// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/correo/api/cuentas/[id]/oauth/iniciar — Pide la URL de consentimiento
 * del proveedor.
 *
 * Devuelve `{authorization_url}`; **no redirige**. Quien tiene que autenticarse
 * ante Google o Microsoft es el administrador en su navegador, no el servidor.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

// El identificador va a la URL del gateway: se valida su FORMA antes de
// interpolarlo. No hay traversal posible tras `encodeURIComponent`, pero un
// valor que no es un UUID solo puede venir de una llamada mal construida, y
// devolverlo como 400 es más útil que un 404 del backend.
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  if (!UUID.test(params.id ?? "")) {
    return json(
      { error: "id_invalido", detail: "Identificador de cuenta no válido." },
      { status: 400 },
    );
  }

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": locals.user?.tenant_slug ?? "",
    "Content-Type": "application/json",
  };
  const userId = locals.user?.sub ?? "";
  if (userId) headers["X-User-Id"] = userId;

  const cuerpo = await request.json().catch(() => ({}));
  try {
    const res = await fetch(
      `${API_V1}/email/cuentas/${encodeURIComponent(params.id)}/oauth/iniciar`,
      { method: "POST", headers, body: JSON.stringify(cuerpo) },
    );
    // 204 no trae cuerpo; `res.json()` sobre vacío lanza.
    if (res.status === 204) return new Response(null, { status: 204 });
    return json(await res.json().catch(() => ({})), { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servicio de radicación." },
      { status: 503 },
    );
  }
}
