// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /borradores/api/[id] — Proxy SSR para obtener y editar un borrador (D-05, T-12c).
 *
 * GET   → BorradorResponse (cuerpo_html saneado)
 * PATCH → edita subject, dest_dept, cuerpo; sanea cuerpo antes de reenviar
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { sanitizeHtml, attachSafeCuerpo } from "$lib/server/sanitizeHtml.js";

/** @type {import('./$types').RequestHandler} */
// El identificador va CODIFICADO al construir la ruta hacia el gateway.
//
// Sin codificar, un `?`, un `#` o un `%00` en el segmento reescriben la
// consulta o el fragmento de la petición que este proxy emite CON EL TOKEN DEL
// USUARIO: la ruta que el navegador pidió y la que el gateway recibe dejan de
// ser la misma. El gateway ya rechaza esas rutas, pero eso es su defensa, no la
// de aquí — quien construye una URL con datos de fuera la codifica.
export async function GET({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";

  try {
    const res = await fetch(`${API_V1}/borradores/${encodeURIComponent(params.id)}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });

    const data = await res.json().catch(() => ({}));
    if (res.ok) attachSafeCuerpo(data);
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de borradores." },
      { status: 503 },
    );
  }
}

/** @type {import('./$types').RequestHandler} */
export async function PATCH({ locals, params, request }) {
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

  // Allowlist explícita — los campos editables del borrador; nunca reenviar estado ni IDs.
  const { subject, dest_dept, cuerpo: rawCuerpo } = body ?? {};

  // T-12c: sanear el cuerpo HTML antes de reenviarlo al gateway (D-05).
  const cleanCuerpo = sanitizeHtml(typeof rawCuerpo === "string" ? rawCuerpo : "") || null;

  const payload = {};
  if (typeof subject === "string") payload.subject = subject.trim();
  if (typeof dest_dept === "string") payload.dest_dept = dest_dept.trim() || null;
  payload.cuerpo = cleanCuerpo;

  try {
    const res = await fetch(`${API_V1}/borradores/${encodeURIComponent(params.id)}`, {
      method: "PATCH",
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
      { error: "connection_error", detail: "No se pudo conectar al servidor de borradores." },
      { status: 503 },
    );
  }
}
