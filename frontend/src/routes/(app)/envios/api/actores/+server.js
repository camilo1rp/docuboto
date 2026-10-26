// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /envios/api/actores?radicado= — el destinatario CONGELADO de un radicado.
 *
 * Por qué existe: registrar un envío pedía teclear destinatario y dirección por
 * TERCERA vez —después del catálogo y del actor congelado del radicado—, sin
 * ninguna relación con lo que el radicado ya dice. Tres oportunidades de que la
 * misma persona quede escrita de tres maneras.
 *
 * Lo que devuelve es **la copia congelada**, no la ficha vigente del catálogo:
 * la pregunta que responde es «a dónde iba este radicado», no «dónde vive hoy
 * esa persona». Son preguntas distintas y confundirlas es el defecto que la
 * copia congelada existe para impedir.
 *
 * El envío lo **precarga y deja editarlo**: la dirección de ESE envío puede
 * diferir legítimamente («remítanlo a mi oficina»), y lo corregido se queda en
 * el envío sin tocar al tercero.
 */

import { json } from "@sveltejs/kit";

import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, url, fetch }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const radicado = url.searchParams.get("radicado") ?? "";
  if (!UUID_RE.test(radicado)) {
    return json(
      { error: "validation_error", detail: "Identificador de radicado inválido." },
      { status: 400 },
    );
  }

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": locals.user?.tenant_slug ?? "",
  };
  const userId = locals.user?.sub ?? "";
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/terceros/radicado/${encodeURIComponent(radicado)}/actores`, {
      headers,
    });
    return json(await res.json().catch(() => null), { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudieron leer los actores del radicado." },
      { status: 503 },
    );
  }
}
