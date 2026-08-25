// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /correo/[cuentaId]/api/sincronizar — Trae del buzón los correos nuevos.
 *
 * **No radica nada**: solo pone en la bandeja lo que aún no estaba. Quién radica
 * y con qué número lo decide un funcionario después, correo a correo.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  if (!UUID.test(params.cuentaId ?? "")) {
    return json({ error: "id_invalido", detail: "Cuenta no válida." }, { status: 400 });
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
      `${API_V1}/email/cuentas/${encodeURIComponent(params.cuentaId)}/bandeja/sincronizar`,
      { method: "POST", headers, body: JSON.stringify(cuerpo) },
    );
    return json(await res.json().catch(() => ({})), { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al buzón." },
      { status: 503 },
    );
  }
}
