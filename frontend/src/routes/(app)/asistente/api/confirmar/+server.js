// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /asistente/api/confirmar — Proxy SSR: ejecuta un acto que la persona aprobó.
 *
 * Es el otro extremo del gate de escritura: `/asistente/api/message` trae una
 * propuesta firmada que NO ha ejecutado nada, y esto la lleva a cabo.
 *
 * **El cuerpo lleva solo el token.** Los argumentos del acto salen del token
 * firmado en el backend, nunca de aquí. Si este proxy aceptara argumentos, la
 * tarjeta describiría un acto y se ejecutaría otro — que es exactamente lo que
 * el diseño existe para impedir. Por eso la allowlist tiene un solo campo, y no
 * es una precaución genérica contra mass-assignment: es la garantía.
 *
 * D-05 (el cliente se trata como hostil): el token de sesión vive solo en
 * `locals`/cookie httpOnly. El JS del navegador nunca lo ve.
 *
 * POST → { token: string }
 *   200: { kind:"result", ejecutado:true, operacion, status, data }
 *   401: sesión no válida · 400: propuesta ausente o malformada
 *   403/409/410: la propuesta caducó, ya se usó, o le retiraron el permiso
 *                entre proponer y confirmar (el backend reautoriza al ejecutar)
 *   503: asistente no disponible
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/** Un token de propuesta (`op1.<payload>.<firma>`) no es un texto libre. */
const MAX_TOKEN_LEN = 4096;

function unavailableResponse() {
  return json(
    { error: "assistant_unavailable", detail: "El asistente aún no está disponible." },
    { status: 503 },
  );
}

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, request }) {
  const sesion = locals.accessToken ?? "";
  if (!sesion) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const token = body?.token;
  if (typeof token !== "string" || !token.trim() || token.length > MAX_TOKEN_LEN) {
    return json(
      {
        error: "propuesta_invalida",
        detail: "Falta la propuesta que se quiere confirmar.",
      },
      { status: 400 },
    );
  }

  // La frase de los actos irreversibles. NO es un argumento del acto —el acto
  // entero va firmado en el token—: es la decisión de la persona, y el backend
  // la exige a partir de la tool que saca del propio token. Sin reenviarla, un
  // acto irreversible sería imposible de confirmar desde la web: la allowlist,
  // hecha para impedir que se cuelen argumentos, se habría llevado por delante
  // la única pieza que sí tiene que pasar.
  const frase = typeof body?.frase === "string" ? body.frase.trim().slice(0, 64) : "";

  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${sesion}`,
    "X-Tenant-Slug": locals.user?.tenant_slug ?? "",
  };
  const userId = locals.user?.sub ?? "";
  const username = locals.user?.preferred_username ?? "";
  if (userId) headers["X-User-Id"] = userId;
  if (username) headers["X-Username"] = username;

  let res;
  try {
    res = await fetch(`${API_V1}/assistant/confirmar`, {
      method: "POST",
      headers,
      // SOLO el token y la frase. Ver la cabecera de este archivo.
      body: JSON.stringify({ token: token.trim(), ...(frase ? { frase } : {}) }),
    });
  } catch {
    return unavailableResponse();
  }

  if (res.status === 404 || res.status === 501 || res.status >= 502) {
    return unavailableResponse();
  }

  const data = await res.json().catch(() => null);
  if (data === null) {
    return unavailableResponse();
  }
  return json(data, { status: res.status });
}
