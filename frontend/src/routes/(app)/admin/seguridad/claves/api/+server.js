// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/seguridad/claves/api — Proxy SSR para la revocación ADMINISTRATIVA
 * de la credencial de firma personal PKI de OTRO usuario (E17/F4, D-05).
 *
 * POST { user_id, motivo?, confirm } →
 *   POST /api/v1/auth/admin/users/{user_id}/signing-key/revoke
 *   → { estado: 'revocado' }
 *
 * Gate real: `USUA_PERM_ADMIN` a nivel de `admin_router` en
 * `services/auth-service/app/routers/signing_key.py` — este proxy NO
 * decide autorización, solo la refleja en la navegación (`navItems.js`);
 * el backend siempre re-valida.
 *
 * `confirm` es una SEGUNDA barrera server-side (el cliente es
 * potencialmente hostil): debe coincidir con `ADMIN_REVOKE_CONFIRM_PHRASE`
 * — mismo patrón que `admin/flujos/reasignacion/api/+server.js`. Revocar
 * la credencial de otro usuario, sin que ese usuario lo sepa de antemano,
 * exige una confirmación explícita tecleada, no solo un clic.
 *
 * `user_id` viaja en el body (no en la ruta) porque esta pantalla no tiene
 * una vista por-usuario; se escapa igualmente al componerlo en la URL del
 * gateway.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { isAdminRevokeConfirmed } from "$lib/utils/signingKeyConfirm.js";

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  // Allowlist: solo los tres campos del contrato de esta pantalla.
  const { user_id: targetUserId, motivo, confirm } = body ?? {};

  if (typeof targetUserId !== "string" || !targetUserId.trim()) {
    return json(
      { error: "validation_error", detail: "Debe seleccionar un usuario." },
      { status: 422 },
    );
  }

  if (!isAdminRevokeConfirmed(confirm)) {
    return json(
      {
        error: "confirmacion_requerida",
        detail: 'Debe escribir la frase de confirmación exacta para revocar la credencial.',
      },
      { status: 422 },
    );
  }

  /** @type {Record<string, unknown>} */
  const payload = {};
  if (typeof motivo === "string" && motivo.trim()) {
    payload.motivo = motivo.trim().slice(0, 500);
  }

  try {
    const res = await fetch(
      `${API_V1}/auth/admin/users/${encodeURIComponent(targetUserId)}/signing-key/revoke`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
          "X-Tenant-Slug": tenant,
          ...(userId ? { "X-User-Id": userId } : {}),
        },
        body: JSON.stringify(payload),
      },
    );
    const data = await res.json().catch(() => ({}));

    if (res.status === 404) {
      return json(
        {
          error: "no_signing_key",
          detail: "Ese usuario no tiene una credencial de firma para revocar.",
        },
        { status: 404 },
      );
    }

    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de autenticación." },
      { status: 503 },
    );
  }
}
