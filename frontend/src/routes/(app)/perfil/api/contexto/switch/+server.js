// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /perfil/api/contexto/switch — Proxy SSR para cambiar la dependencia
 * activa del propio usuario (E08, ADR-013, D-05).
 *
 * POST { depe_id } → POST /api/v1/auth/context/switch
 *   → { user_id, active_depe_id, available_depts: URDResponse[] }
 *
 * El cambio de contexto NO reemite el JWT (ADR-013): la dependencia activa
 * se persiste en `auth-service` y se relee en cada `/auth/context`/`/auth/me`.
 * Por eso el llamador (perfil/+page.svelte y AppLayout.svelte, vía
 * `DependencySwitcher.svelte`) DEBE invalidar el `load` de
 * `(app)/+layout.server.js` (`invalidateAll()`) tras un cambio exitoso —
 * de lo contrario la navegación seguiría mostrando la dependencia/rol
 * anteriores aunque el backend ya cambió de contexto.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

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

  // Allowlist: un solo campo del contrato.
  const { depe_id } = body ?? {};
  const depeIdInt = Number(depe_id);
  if (!Number.isInteger(depeIdInt) || depeIdInt < 1 || depeIdInt > 999999) {
    return json({ error: "validation_error", detail: "depe_id inválido." }, { status: 422 });
  }

  try {
    const res = await fetch(`${API_V1}/auth/context/switch`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
        ...(userId ? { "X-User-Id": userId } : {}),
      },
      body: JSON.stringify({ depe_id: depeIdInt }),
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
