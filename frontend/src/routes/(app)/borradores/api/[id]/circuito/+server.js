// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /borradores/api/[id]/circuito — abrir y consultar el circuito de aprobación
 * y firma de un borrador (E23 Inc.4, RF-PRO-10).
 *
 * POST → POST /api/v1/signature/requests   { objeto_tipo: "borrador", ... }
 * GET  → GET  /api/v1/signature/requests?objeto_tipo=borrador&objeto_id=…
 *
 * Cierra el hueco de que NINGUNA pantalla creaba cadenas de firma: el endpoint
 * del gateway existía y solo lo invocaban los tests, así que `/firmas` era una
 * bandeja que firmaba lo que otro creaba, y no había otro.
 *
 * **La huella NO viaja desde aquí.** signature-service la resuelve él mismo
 * contra document-service e ignora la del cuerpo: una huella que el llamante
 * elige no vincula a nadie — bastaría con enviar la del documento anterior para
 * que la cadena siguiera pareciendo válida tras editar el texto. Este proxy no
 * tiene dónde ponerla, y esa ausencia es la garantía.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const ROLES = new Set(["visto_bueno", "firma"]);

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params, request }) {
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

  const turnos = Array.isArray(body?.turnos) ? body.turnos : [];
  if (turnos.length === 0) {
    return json(
      { error: "validation_error", detail: "El circuito necesita al menos un turno." },
      { status: 400 },
    );
  }
  if (!turnos.every((t) => typeof t?.firmante_id === "string" && ROLES.has(t?.rol))) {
    return json(
      {
        error: "validation_error",
        detail: "Cada turno necesita un firmante y un rol («visto_bueno» o «firma»).",
      },
      { status: 400 },
    );
  }
  // Se comprueba aquí además de en el backend para dar el mensaje en el idioma
  // de la pantalla; la garantía sigue siendo la del servidor.
  if (!turnos.some((t) => t.rol === "firma")) {
    return json(
      {
        error: "validation_error",
        detail:
          "El circuito necesita un turno de firma: sin él no hay documento definitivo " +
          "y el borrador no podría salir.",
      },
      { status: 400 },
    );
  }

  try {
    const res = await fetch(`${API_V1}/signature/requests`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
      body: JSON.stringify({
        objeto_tipo: "borrador",
        objeto_id: params.id,
        titulo: typeof body?.titulo === "string" ? body.titulo : null,
        firmantes: turnos.map((t) => t.firmante_id),
        roles: turnos.map((t) => t.rol),
      }),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "gateway_unreachable", detail: "No se pudo contactar el servicio." },
      { status: 502 },
    );
  }
}

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";

  const qs = new URLSearchParams({ objeto_tipo: "borrador", objeto_id: params.id });
  try {
    const res = await fetch(`${API_V1}/signature/requests?${qs}`, {
      headers: { Authorization: `Bearer ${token}`, "X-Tenant-Slug": tenant },
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "gateway_unreachable", detail: "No se pudo contactar el servicio." },
      { status: 502 },
    );
  }
}
