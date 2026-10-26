// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /asistente/api/transcribe — Proxy SSR del dictado por servidor (ADR-020).
 *
 * Es el camino que funciona en CUALQUIER navegador. `VoiceInput` prefiere el
 * reconocedor del dispositivo cuando existe, pero en Chrome y Edge la Web Speech
 * API clásica manda el audio a un servicio de Google, así que allí no se ofrece:
 * para esos navegadores, la voz viaja por aquí y no sale de la entidad.
 *
 * D-05: el token vive solo en `locals`/cookie httpOnly. El JS del navegador
 * nunca lo ve — manda el audio a este proxy y ya.
 *
 * GET  → { disponible, proveedor, procesa_en }   (capacidad; sin sesión no aplica)
 * POST → multipart con `file` (y `idioma`) → { texto, idioma, proveedor, modelo }
 *
 * El audio se reenvía TAL CUAL, sin reempaquetar: cualquier conversión
 * intermedia es una oportunidad de perder o alterar lo que la persona dijo.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/** Un dictado son segundos. El backend tiene su propio tope; este evita
 *  siquiera cruzar la red con algo que va a rechazar. */
const MAX_BYTES = 10 * 1024 * 1024;

function noDisponible(detalle) {
  return json({ error: "stt_no_disponible", detail: detalle }, { status: 503 });
}

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  try {
    const res = await fetch(`${API_V1}/assistant/transcribe`, {
      headers: { Authorization: `Bearer ${locals.accessToken}` },
    });
    if (!res.ok) return json({ disponible: false, proveedor: "desconocido" }, { status: 200 });
    return json(await res.json(), { status: 200 });
  } catch {
    // El backend no responde: se declara NO disponible en vez de dejar que la
    // interfaz ofrezca un botón y falle al pulsarlo.
    return json({ disponible: false, proveedor: "desconocido" }, { status: 200 });
  }
}

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  let entrada;
  try {
    entrada = await request.formData();
  } catch {
    return json({ error: "invalid_body", detail: "Se esperaba un audio." }, { status: 400 });
  }

  const audio = entrada.get("file");
  if (!audio || typeof audio === "string") {
    return json({ error: "audio_ausente", detail: "No llegó audio." }, { status: 400 });
  }
  if (audio.size > MAX_BYTES) {
    return json(
      {
        error: "audio_demasiado_grande",
        detail: "El dictado es para una frase, no para una grabación larga.",
      },
      { status: 413 },
    );
  }

  // Allowlist campo a campo, igual que el resto de proxies: se reconstruye el
  // formulario en vez de reenviar el del cliente entero.
  const salida = new FormData();
  salida.append("file", audio, "dictado.webm");
  const idioma = entrada.get("idioma");
  salida.append("idioma", typeof idioma === "string" && idioma ? idioma.slice(0, 12) : "es");

  let res;
  try {
    res = await fetch(`${API_V1}/assistant/transcribe`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": locals.user?.tenant_slug ?? "",
      },
      body: salida,
    });
  } catch {
    return noDisponible("No se pudo contactar con el servicio de transcripción.");
  }

  const datos = await res.json().catch(() => null);
  if (datos === null) return noDisponible("Respuesta ilegible del servicio de transcripción.");
  return json(datos, { status: res.status });
}
