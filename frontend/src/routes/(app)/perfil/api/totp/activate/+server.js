// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /perfil/api/totp/activate — Proxy SSR para activar un enrolamiento TOTP
 * pendiente con el primer código generado por la app autenticadora.
 *
 * POST { code } → POST /api/v1/auth/me/totp/activate → { estado, activated_at }
 *
 * Validación de forma (6 dígitos) server-side por defensa en profundidad
 * — el backend es quien decide si el código es válido/vigente.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const CODE_RE = /^\d{6}$/;

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

  const { code } = body ?? {};
  if (typeof code !== "string" || !CODE_RE.test(code)) {
    return json(
      { error: "validation_error", detail: "El código debe tener exactamente 6 dígitos." },
      { status: 422 },
    );
  }

  try {
    const res = await fetch(`${API_V1}/auth/me/totp/activate`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
        ...(userId ? { "X-User-Id": userId } : {}),
      },
      body: JSON.stringify({ code }),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de autenticación." },
      { status: 503 },
    );
  }
}
