// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/usuarios/api/[id]/totp — Restablecimiento administrativo del 2.º factor.
 *
 * DELETE → `POST /api/v1/auth/users/{id}/totp/revoke` (USUA_PERM_ADMIN).
 *   Deja a la persona SIN segundo factor para que lo vuelva a registrar ella
 *   misma desde `/perfil`. NUNCA devuelve secreto ni enrola en nombre de nadie.
 *
 * Por qué existe: sin esta vía, quien perdía el autenticador quedaba encerrado
 * —re-enrolar y revocar exigen el código actual o uno de recuperación, y los de
 * recuperación no sirven para firmar—, y la única salida era un UPDATE a mano
 * en la base de datos.
 *
 * El `motivo` NO se inventa aquí ni se rellena con un texto por defecto: lo
 * escribe quien restablece, y es lo que hace auditable el acto. Un valor
 * puesto por el proxy sería una firma en blanco.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MOTIVO_MIN = 8;
const MOTIVO_MAX = 500;

/** @type {import('./$types').RequestHandler} */
export async function DELETE({ locals, params, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  const { id } = params;
  if (!id || !UUID_RE.test(id)) {
    return json(
      { error: "validation_error", detail: "Identificador de usuario inválido." },
      { status: 400 },
    );
  }

  const body = await request.json().catch(() => ({}));
  const motivo = typeof body?.motivo === "string" ? body.motivo.trim() : "";
  if (motivo.length < MOTIVO_MIN || motivo.length > MOTIVO_MAX) {
    return json(
      {
        error: "validation_error",
        detail: `El motivo debe explicar el restablecimiento (entre ${MOTIVO_MIN} y ${MOTIVO_MAX} caracteres).`,
      },
      { status: 400 },
    );
  }

  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
    "Content-Type": "application/json",
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/auth/users/${encodeURIComponent(id)}/totp/revoke`, {
      method: "POST",
      headers,
      body: JSON.stringify({ motivo }),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      {
        error: "connection_error",
        detail: "No se pudo restablecer el segundo factor.",
      },
      { status: 503 },
    );
  }
}
