// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /envios/api/crear — Proxy SSR para crear un nuevo envío postal (D-05, T-12e).
 *
 * POST { radicado_id, operador, destinatario?, direccion? }
 *   → POST /api/v1/documents/{radicado_id}/envios
 *   → ShipmentResponse (201)
 *
 * Allowlist explícita: solo se reenvían los campos del contrato ShipmentCreate.
 * `radicado_id` va en la ruta del gateway (no en el body); el cuerpo solo lleva
 * operador, destinatario y direccion.
 *
 * X-User-Id: actor traceability (D-05, consistente con aprobar/radicar/transferir).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Resuelve un número de radicado a su UUID usando la misma búsqueda que el
 * resto del sistema. Devuelve `null` si no hay coincidencia EXACTA: una
 * aproximada elegiría por su cuenta a qué documento se envía correo físico.
 *
 * @param {typeof globalThis.fetch} fetchFn
 * @param {string} numero
 * @param {Record<string, string>} headers
 * @returns {Promise<string|null>}
 */
async function resolverPorNumero(fetchFn, numero, headers) {
  try {
    const res = await fetchFn(`${API_V1}/search?q=${encodeURIComponent(numero)}&size=10`, {
      headers,
    });
    if (!res.ok) return null;
    const data = await res.json().catch(() => null);
    const hits = data?.results ?? data?.hits ?? [];
    const exacto = hits.find(
      (h) => String(h?.tracking_number ?? "").toUpperCase() === numero.toUpperCase(),
    );
    return exacto?.id ?? null;
  } catch {
    return null;
  }
}

const OPERADORES_VALIDOS = new Set(["4-72", "servientrega", "otro"]);

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

  // Allowlist explícita (ShipmentCreate).
  const { radicado_id, operador, destinatario, direccion } = body ?? {};

  if (!radicado_id || typeof radicado_id !== "string" || !radicado_id.trim()) {
    return json(
      { error: "validation_error", detail: "Indique el radicado de salida." },
      { status: 422 },
    );
  }

  if (!operador || !OPERADORES_VALIDOS.has(operador)) {
    return json(
      { error: "validation_error", detail: "Operador no válido. Use: 4-72, servientrega u otro." },
      { status: 422 },
    );
  }

  // Payload para el gateway — solo operador, destinatario y direccion (radicado_id va en la ruta).
  const payload = {
    operador,
    destinatario:
      typeof destinatario === "string" && destinatario.trim() ? destinatario.trim() : null,
    direccion: typeof direccion === "string" && direccion.trim() ? direccion.trim() : null,
  };

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  // El identificador que el gateway necesita es el UUID; el que la persona
  // tiene delante es el NÚMERO DE RADICADO. Antes el formulario pedía el UUID
  // «disponible en el detalle del radicado en la Bandeja» — y ese detalle no lo
  // muestra ni deja copiarlo, así que registrar un envío desde la interfaz era
  // sencillamente imposible: había que sacarlo de la base de datos. Se resuelve
  // aquí, en el servidor, donde ya está el token: la pantalla vuelve a pedir lo
  // que la persona conoce.
  let radicadoUuid = radicado_id.trim();
  if (!UUID_RE.test(radicadoUuid)) {
    const resuelto = await resolverPorNumero(fetch, radicadoUuid, headers);
    if (!resuelto) {
      return json(
        {
          error: "radicado_no_encontrado",
          detail: `No se encontró ningún radicado de salida con el número «${radicadoUuid}».`,
        },
        { status: 404 },
      );
    }
    radicadoUuid = resuelto;
  }

  try {
    const res = await fetch(`${API_V1}/documents/${encodeURIComponent(radicadoUuid)}/envios`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });

    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de envíos." },
      { status: 503 },
    );
  }
}
