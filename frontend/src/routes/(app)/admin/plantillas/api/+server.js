// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/plantillas/api — Proxy SSR para listar y crear plantillas (D-05, T-12c).
 *
 * GET  ?activo=  → PlantillaResponse[] (cuerpo_html saneado). Requiere PERM_RADI.
 *                  También usado desde el selector de plantillas en borradores.
 * POST           → crea una plantilla; body: { nombre, cuerpo, tipo_documental?,
 *                  activo? }. Requiere USUA_PERM_ADMIN. El cuerpo HTML se SANEA
 *                  antes de reenviar al gateway (D-05).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { sanitizeHtml, attachSafeCuerpo } from "$lib/server/sanitizeHtml.js";

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";

  const activo = url.searchParams.get("activo") ?? "";
  const params = new URLSearchParams();
  if (activo) params.set("activo", activo);

  try {
    const res = await fetch(`${API_V1}/plantillas?${params}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });

    const data = await res.json().catch(() => []);
    if (!res.ok) return json(data, { status: res.status });

    if (Array.isArray(data)) data.forEach(attachSafeCuerpo);
    return json(data);
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de plantillas." },
      { status: 503 },
    );
  }
}

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  // Allowlist explícita — nunca reenviar id, created_at ni campos derivados.
  const { nombre, tipo_documental, cuerpo: rawCuerpo, activo } = body ?? {};

  // T-12c: sanear el cuerpo HTML antes de enviarlo al gateway (D-05).
  const cleanCuerpo = sanitizeHtml(typeof rawCuerpo === "string" ? rawCuerpo : "") || null;

  const payload = {
    nombre: typeof nombre === "string" ? nombre.trim() : "",
    tipo_documental:
      typeof tipo_documental === "string" && tipo_documental.trim()
        ? tipo_documental.trim()
        : null,
    cuerpo: cleanCuerpo,
    activo: typeof activo === "boolean" ? activo : true,
  };

  try {
    const res = await fetch(`${API_V1}/plantillas`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
      body: JSON.stringify(payload),
    });

    const data = await res.json().catch(() => ({}));
    if (res.ok) attachSafeCuerpo(data);
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de plantillas." },
      { status: 503 },
    );
  }
}
