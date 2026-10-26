// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/correo/api/cuentas — Proxy SSR de las cuentas de correo (E19).
 *
 * `GET` lista las que el usuario alcanza; `POST` da de alta una.
 *
 * El cuerpo del alta se reenvía tal cual: la validación vive en el backend
 * —donde está el esquema y las restricciones de la base—, y duplicarla aquí
 * crearía dos listas de reglas que divergirían. Lo que sí se hace es NO tocar la
 * contraseña: viaja del formulario al servicio y se cifra allí; el proxy no la
 * registra ni la retiene.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

function cabeceras(locals) {
  const token = locals.accessToken ?? "";
  if (!token) return null;
  /** @type {Record<string, string>} */
  const h = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": locals.user?.tenant_slug ?? "",
    "Content-Type": "application/json",
  };
  const userId = locals.user?.sub ?? "";
  if (userId) h["X-User-Id"] = userId;
  return h;
}

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals }) {
  const headers = cabeceras(locals);
  if (!headers)
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  try {
    const res = await fetch(`${API_V1}/email/cuentas`, { headers });
    return json(await res.json().catch(() => []), { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servicio de radicación." },
      { status: 503 },
    );
  }
}

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, request }) {
  const headers = cabeceras(locals);
  if (!headers)
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  const cuerpo = await request.json().catch(() => ({}));
  try {
    const res = await fetch(`${API_V1}/email/cuentas`, {
      method: "POST",
      headers,
      body: JSON.stringify(cuerpo),
    });
    return json(await res.json().catch(() => ({})), { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servicio de radicación." },
      { status: 503 },
    );
  }
}
