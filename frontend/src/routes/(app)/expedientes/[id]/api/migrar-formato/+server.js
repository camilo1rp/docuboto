// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Proxy SSR de `POST /api/v1/preservacion/migrar-formato` (E10, RF-PRE-04).
 *
 * Produce una **copia de preservación** en PDF/A sin tocar el original
 * (Decreto 2609/2012 art. 32).
 *
 * **El `503` se reenvía con su cuerpo, y es el caso que más importa**: dice que
 * este despliegue no tiene convertidor instalado, que es un problema de
 * instalación y no del documento. Colapsarlo a «no se pudo migrar» mandaría a
 * depurar el PDF equivocado.
 *
 * `perfil` pasa por una lista CERRADA: solo las variantes `b` de PDF/A, porque
 * las `a`/`u` exigen texto etiquetado o Unicode garantizado y una conversión
 * automática no puede prometerlo.
 */
import { json } from "@sveltejs/kit";

import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const PERFILES = new Set(["1b", "2b", "3b"]);

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, request, fetch }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  const body = await request.json().catch(() => ({}));
  const fileId = String(body?.file_id ?? "");
  if (!UUID_RE.test(fileId)) {
    return json(
      { error: "validation_error", detail: "Identificador de archivo inválido." },
      { status: 400 },
    );
  }
  const perfil = PERFILES.has(body?.perfil) ? body.perfil : "2b";

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": locals.user?.tenant_slug ?? "",
    "Content-Type": "application/json",
  };
  if (locals.user?.sub) headers["X-User-Id"] = locals.user.sub;

  try {
    const res = await fetch(`${API_V1}/preservacion/migrar-formato`, {
      method: "POST",
      headers,
      body: JSON.stringify({ file_id: fileId, perfil }),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de preservación." },
      { status: 503 },
    );
  }
}
