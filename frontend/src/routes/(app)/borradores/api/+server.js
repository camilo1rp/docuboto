// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /borradores/api — Proxy SSR para listar y crear borradores (D-05, T-12c).
 *
 * El token se lee de locals.accessToken (hook); nunca se expone al cliente.
 *
 * GET  ?estado=  → lista de BorradorResponse[] (cuerpo_html saneado)
 * POST           → crea un nuevo borrador; body: { doc_type, subject, dest_dept_code?,
 *                  cuerpo?, plantilla_id? }. El cuerpo HTML se SANEA antes de
 *                  reenviar al gateway (D-05).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { parseCodigoDependencia } from "$lib/utils/dependenciaNombre.js";
import { sanitizeHtml, attachSafeCuerpo } from "$lib/server/sanitizeHtml.js";

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";

  const estado = url.searchParams.get("estado") ?? "";
  const params = new URLSearchParams();
  if (estado) params.set("estado", estado);

  try {
    const res = await fetch(`${API_V1}/borradores?${params}`, {
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
      { error: "connection_error", detail: "No se pudo conectar al servidor de borradores." },
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

  // Allowlist explícita — nunca reenviar campos de control (estado, radicado_id, id…).
  const { doc_type, subject, dest_dept_code, cuerpo: rawCuerpo, plantilla_id } = body ?? {};

  // T-12c: sanear el cuerpo HTML antes de enviarlo al gateway (D-05).
  const cleanCuerpo = sanitizeHtml(typeof rawCuerpo === "string" ? rawCuerpo : "") || null;

  const payload = {
    doc_type: doc_type === "I" ? "I" : "S",
    subject: typeof subject === "string" ? subject.trim() : "",
    // ADR-032: el destino viaja por código; el servidor fija el nombre (instantánea).
    dest_dept_code: parseCodigoDependencia(dest_dept_code),
    cuerpo: cleanCuerpo,
    plantilla_id: typeof plantilla_id === "string" && plantilla_id ? plantilla_id : null,
  };

  try {
    const res = await fetch(`${API_V1}/borradores`, {
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
      { error: "connection_error", detail: "No se pudo conectar al servidor de borradores." },
      { status: 503 },
    );
  }
}
