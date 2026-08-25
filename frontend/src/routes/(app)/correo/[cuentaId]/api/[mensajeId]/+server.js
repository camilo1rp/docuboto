// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /correo/[cuentaId]/api/[mensajeId] — Acciones sobre un correo de la bandeja.
 *
 * Una sola ruta con `?accion=` en vez de tres subrutas: las tres comparten
 * validación, cabeceras y traducción de errores, y separarlas triplicaría ese
 * andamiaje para variar una palabra de la URL.
 *
 * La acción se resuelve contra una **lista cerrada**; cualquier otra cosa es un
 * 400 antes de tocar el gateway. Se usa `Object.hasOwn` y no un acceso directo
 * porque `ACCIONES["constructor"]` devolvería un miembro heredado de
 * `Object.prototype` en vez de `undefined`, y el guard dejaría de ser la lista
 * cerrada que aparenta ser.
 *
 * Respuestas de negocio que el cliente distingue, y que **no son averías**:
 *   - 409 correo_ocupado    → otro funcionario lo está trabajando.
 *   - 409 sin_reserva       → la reserva caducó o la tiene otro.
 *   - 409 correo_ya_resuelto→ alguien lo radicó o descartó mientras tanto.
 * Las tres significan que la reserva está haciendo su trabajo.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ACCIONES = { reservar: "reservar", radicar: "radicar", descartar: "descartar" };

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params, url, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  if (!UUID.test(params.cuentaId ?? "") || !UUID.test(params.mensajeId ?? "")) {
    return json({ error: "id_invalido", detail: "Identificador no válido." }, { status: 400 });
  }

  const pedida = url.searchParams.get("accion") ?? "";
  const accion = Object.hasOwn(ACCIONES, pedida) ? ACCIONES[pedida] : undefined;
  if (!accion) {
    return json(
      { error: "accion_desconocida", detail: `Acción no admitida: ${pedida}` },
      { status: 400 },
    );
  }

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": locals.user?.tenant_slug ?? "",
    "Content-Type": "application/json",
  };
  const userId = locals.user?.sub ?? "";
  if (userId) headers["X-User-Id"] = userId;

  const cuerpo = await request.json().catch(() => ({}));
  try {
    const res = await fetch(
      `${API_V1}/email/cuentas/${encodeURIComponent(params.cuentaId)}/bandeja/` +
        `${encodeURIComponent(params.mensajeId)}/${accion}`,
      { method: "POST", headers, body: JSON.stringify(cuerpo) },
    );
    return json(await res.json().catch(() => ({})), { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor." },
      { status: 503 },
    );
  }
}
