// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * frontAdminEndpoints.js — Qué rutas del gateway consume cada área de /admin.
 *
 * Función PURA sobre el texto de un `+page.server.js` / `+server.js`. Reconoce:
 *   · `${API_V1}/ruta/${param}?qs` — los `${…}` que son un segmento completo pasan a
 *     `{}`; los que cuelgan de un segmento (`…${qs ? "?" : ""}`) se descartan.
 *   · `${API_V1}/${servicePath}/…` / `${SERVICE_PATHS[…]}` — se expande con los
 *     valores del `SERVICE_PATHS = {…}` del mismo archivo (cola de eventos fallidos).
 *   · `${API_V1}${ruta}` (ruta calculada): en ese archivo se toman como GET los
 *     literales `"/segmento…"` que no son rutas del BFF (`/admin`, `/api`, `/login`).
 *
 * El método es el `method: "X"` de la MISMA llamada (hasta el `)` que la cierra);
 * sin él, GET.
 */

const PREFIJOS_NO_API = ["/admin", "/api/", "/login", "/logout", "/auth"];

/** @param {string} ruta */
function normalizar(ruta) {
  const r = ruta.replace(/\?.*$/, "").replace(/\/+$/, "");
  return r === "" ? "/" : r;
}

/**
 * Lee la plantilla desde `i` (justo tras `${API_V1}`) hasta la comilla de cierre.
 * @param {string} src
 * @param {number} i
 * @returns {string}  p. ej. `/catalogos/{}` o `/{SERVICE}/x`
 */
function leerPlantilla(src, i) {
  let out = "";
  while (i < src.length) {
    const c = src[i];
    if (c === "`" || c === '"' || c === "\n") break;
    if (c === "$" && src[i + 1] === "{") {
      let prof = 1;
      let j = i + 2;
      while (j < src.length && prof > 0) {
        if (src[j] === "{") prof++;
        else if (src[j] === "}") prof--;
        j++;
      }
      const expr = src.slice(i + 2, j - 1);
      // Solo es un segmento de la ruta si ocupa el segmento entero; un
      // `${qs ? …}` pegado a un segmento es la query y se descarta.
      if (out.endsWith("/") && /^[/?`"]$/.test(src[j] ?? "")) {
        out += /\bservicePath\b|SERVICE_PATHS/.test(expr) ? "{SERVICE}" : "{}";
      }
      i = j;
      continue;
    }
    if (c === "?") break;
    out += c;
    i++;
  }
  return out;
}

/**
 * @param {string} fuente
 * @param {string} archivo etiqueta
 * @returns {{ metodo: string, ruta: string, archivo: string, linea: number }[]}
 */
export function extraerEndpointsFront(fuente, archivo) {
  /** @type {{ metodo: string, ruta: string, archivo: string, linea: number }[]} */
  const out = [];
  const lineaDe = (/** @type {number} */ idx) => fuente.slice(0, idx).split("\n").length;

  // Valores de SERVICE_PATHS (si el archivo lo declara).
  const bloque = fuente.match(/SERVICE_PATHS\s*=\s*\{([^}]*)\}/);
  const servicios = bloque ? [...bloque[1].matchAll(/:\s*"([^"]+)"/g)].map((m) => m[1]) : [];

  const dinamico = /\$\{API_V1\}\$\{/.test(fuente);

  for (const m of fuente.matchAll(/\$\{API_V1\}(?!\$\{)/g)) {
    const idx = m.index ?? 0;
    const desde = idx + m[0].length;
    const plantilla = leerPlantilla(fuente, desde);
    if (!plantilla.startsWith("/")) continue;
    // Método: dentro de la llamada que contiene este URL.
    let prof = 0;
    let fin = fuente.length;
    for (let k = desde; k < fuente.length; k++) {
      const c = fuente[k];
      if (c === "(") prof++;
      else if (c === ")") {
        if (prof === 0) {
          fin = k;
          break;
        }
        prof--;
      }
    }
    const ventana = fuente.slice(desde, fin);
    let mm = ventana.match(/\bmethod:\s*"(GET|POST|PATCH|PUT|DELETE)"/);
    if (!mm) {
      // `fetch(url, init)`: el método vive en el objeto `init` declarado antes.
      const ident = ventana.match(/,\s*([A-Za-z_]\w*)\s*$/);
      if (ident)
        mm = fuente
          .slice(0, idx)
          .match(
            new RegExp(
              `\\b${ident[1]}\\s*=\\s*\\{[^}]*?\\bmethod:\\s*"(GET|POST|PATCH|PUT|DELETE)"`,
            ),
          );
    }
    const metodo = mm ? mm[1] : "GET";
    const variantes = plantilla.includes("{SERVICE}")
      ? servicios.map((s) => plantilla.replace("{SERVICE}", s))
      : [plantilla];
    for (const v of variantes)
      out.push({ metodo, ruta: normalizar(v), archivo, linea: lineaDe(idx) });
    if (plantilla.includes("{SERVICE}") && servicios.length === 0)
      out.push({ metodo, ruta: normalizar(plantilla), archivo, linea: lineaDe(idx) });
  }

  if (dinamico) {
    for (const m of fuente.matchAll(/["'`](\/[a-z][A-Za-z0-9_\-/{}$.]*)(?:\?[^"'`]*)?["'`]/g)) {
      const lit = m[1];
      if (PREFIJOS_NO_API.some((p) => lit === p.replace(/\/$/, "") || lit.startsWith(p))) continue;
      if (/\.(js|svelte|css|json)$/.test(lit)) continue;
      const ruta = normalizar(lit.replace(/\$\{[^}]*\}/g, "{}"));
      out.push({ metodo: "GET", ruta, archivo, linea: lineaDe(m.index ?? 0) });
    }
  }
  return out;
}
