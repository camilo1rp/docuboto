// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Proxy SSR del catálogo geográfico DIVIPOLA (E01, RF-RAD-05).
 *
 * Sin `?departamento` devuelve los 33 departamentos; con él, los municipios de
 * ese departamento. **El departamento es obligatorio para pedir municipios**:
 * los 1.135 de golpe serían la lista que nadie recorre, que es justo lo que la
 * cascada evita.
 *
 * Existe para que el municipio deje de ser algo que se escribe. Mientras fue
 * texto libre, «Bogotá», «BOGOTA» y «Bogota D.C.» eran tres municipios para
 * cualquier consulta y cualquier reporte — la misma plaga de duplicados del
 * Orfeo original, vista desde el destino en vez del sujeto.
 */
import { json } from "@sveltejs/kit";

import { API_V1 } from "$lib/utils/config.server.js";

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, url, fetch }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${locals.accessToken}`,
    "X-Tenant-Slug": locals.user?.tenant_slug ?? "",
  };
  if (locals.user?.sub) headers["X-User-Id"] = locals.user.sub;

  const departamento = url.searchParams.get("departamento");
  let destino = `${API_V1}/terceros/divipola/departamentos`;

  if (departamento) {
    // Se rechaza aquí lo que no tiene forma de código: un parámetro libre
    // viajando al backend para que lo rechace es un viaje que ya sabíamos
    // perdido.
    if (!/^[0-9]{2}$/.test(departamento)) {
      return json(
        { error: "validation_error", detail: "Código de departamento inválido." },
        { status: 400 },
      );
    }
    const params = new URLSearchParams({ departamento });
    const q = url.searchParams.get("q");
    if (q) params.set("q", q.slice(0, 120));
    destino = `${API_V1}/terceros/divipola/municipios?${params}`;
  }

  try {
    const res = await fetch(destino, { headers });
    const data = await res.json().catch(() => []);
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al catálogo geográfico." },
      { status: 503 },
    );
  }
}
