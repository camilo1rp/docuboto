// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * gatesFastapi.js — Lector estático de los gates de autorización de los routers FastAPI.
 *
 * Función PURA (recibe el texto de un `.py`, no toca el disco). Devuelve, por cada
 * ruta, el permiso que el backend exige de verdad. Lo consume el guardián
 * `admin-gate-contrato.test.js` para comparar contra `ADMIN_AREA_PERMISSIONS`.
 *
 * Qué entiende:
 *   · `X = APIRouter(prefix="…", dependencies=[Depends(require_permission(…))])`
 *     (multilínea) — el gate de router se hereda en todas sus rutas.
 *   · `@X.<verbo>("<ruta>", dependencies=[…])` — gate en el decorador.
 *   · `Depends(require_permission("P"[, min_crud=N]))` en la firma. La firma se corta
 *     por PARÉNTESIS BALANCEADOS, no por `):\n`: así `-> T:` y las firmas con
 *     parámetros multilínea no mezclan los gates de dos rutas vecinas.
 *   · `min_crud=<constante>` resuelta contra una asignación de módulo (`_NIVEL = 3`).
 *   · `require_any_permission("A", "B"[, min_crud=N])` — gate any-of (basta UNO): el
 *     `Gate` lleva `anyOf: ["A", "B"]` y `permiso` = el primero. Un gate ordinario
 *     lleva `anyOf: [permiso]`. Quien compara contra el mapa debe usar `anyOf`.
 *
 * Qué NO puede leer, y por eso lo MARCA en vez de inventarlo (fail-closed):
 *   · `check_permission(…)` dentro del cuerpo (el permiso se decide en el código).
 *   · `require_permission(min_crud=N)` sin nombre literal.
 *   · `X-Internal-Token` / interoperabilidad pública: no son permisos de rol.
 */

const VERBOS = ["get", "post", "patch", "put", "delete"];

/** Dependencias que NO son un permiso de rol; se reportan aparte. */
const ESPECIALES = ["require_internal_token", "_require_internal_token", "require_public_interop"];

/**
 * Sustituye por espacios los comentarios `#` y el CONTENIDO de las cadenas
 * triple-comilla (docstrings), conservando saltos de línea y longitud. Las
 * cadenas de una línea se dejan: ahí viven las rutas y los nombres de permiso.
 * @param {string} src
 */
export function limpiarPython(src) {
  let out = "";
  let i = 0;
  const n = src.length;
  while (i < n) {
    const c = src[i];
    if (c === "#") {
      while (i < n && src[i] !== "\n") {
        out += " ";
        i++;
      }
    } else if ((c === '"' || c === "'") && src.startsWith(c.repeat(3), i)) {
      const cierre = src.indexOf(c.repeat(3), i + 3);
      const fin = cierre === -1 ? n : cierre + 3;
      out += c.repeat(3);
      for (let k = i + 3; k < fin - 3; k++) out += src[k] === "\n" ? "\n" : " ";
      if (cierre !== -1) out += c.repeat(3);
      i = fin;
    } else if (c === '"' || c === "'") {
      let j = i + 1;
      while (j < n && src[j] !== c && src[j] !== "\n") j += src[j] === "\\" ? 2 : 1;
      out += src.slice(i, j + 1);
      i = j + 1;
    } else {
      out += c;
      i++;
    }
  }
  return out;
}

/**
 * Índice del `)` que cierra el `(` en `iAbre`, saltando cadenas. -1 si no cierra.
 * @param {string} src texto ya limpiado
 * @param {number} iAbre
 */
export function cerrarParentesis(src, iAbre) {
  let prof = 0;
  for (let i = iAbre; i < src.length; i++) {
    const c = src[i];
    if (c === '"' || c === "'") {
      let j = i + 1;
      while (j < src.length && src[j] !== c && src[j] !== "\n") j += src[j] === "\\" ? 2 : 1;
      i = j;
    } else if (c === "(") prof++;
    else if (c === ")") {
      prof--;
      if (prof === 0) return i;
    }
  }
  return -1;
}

/** `{id}` y `{ruta:path}` → `{}`; sin barra final. */
export function normalizarRuta(ruta) {
  const r = ruta.replace(/\{[^}]*\}/g, "{}").replace(/\/+$/, "");
  return r === "" ? "/" : r;
}

/**
 * @typedef {{ permiso: string | null, minCrud: number | null, origen: string, anyOf: string[] }} Gate
 */

/**
 * Gates `require_permission(…)` dentro de un fragmento ya limpiado.
 * @param {string} texto
 * @param {Record<string, number>} constantes
 * @param {string} origen
 * @returns {Gate[]}
 */
function gatesEn(texto, constantes, origen) {
  /** @type {Gate[]} */
  const out = [];
  for (const m of texto.matchAll(/\brequire_permission\(([^()]*)\)/g)) {
    const args = m[1];
    const nombre = args.match(/^\s*["']([A-Z0-9_]+)["']/);
    const crud = args.match(/min_crud\s*=\s*([A-Za-z0-9_]+)/);
    let minCrud = 1;
    if (crud) {
      minCrud = /^\d+$/.test(crud[1]) ? Number(crud[1]) : (constantes[crud[1]] ?? NaN);
    }
    out.push({
      permiso: nombre ? nombre[1] : null,
      minCrud: Number.isFinite(minCrud) ? minCrud : null,
      origen,
      anyOf: nombre ? [nombre[1]] : [],
    });
  }
  // Gate any-of: TODOS los literales de cadena son brazos. Sin ninguno (argumentos
  // dinámicos) se marca `permiso: null` para que el contrato lo trate como no estático.
  for (const m of texto.matchAll(/\brequire_any_permission\(([^()]*)\)/g)) {
    const args = m[1];
    const brazos = [...args.matchAll(/["']([A-Z0-9_]+)["']/g)].map((x) => x[1]);
    const crud = args.match(/min_crud\s*=\s*([A-Za-z0-9_]+)/);
    let minCrud = 1;
    if (crud) {
      minCrud = /^\d+$/.test(crud[1]) ? Number(crud[1]) : (constantes[crud[1]] ?? NaN);
    }
    out.push({
      permiso: brazos[0] ?? null,
      minCrud: Number.isFinite(minCrud) ? minCrud : null,
      origen,
      anyOf: brazos,
    });
  }
  return out;
}

/** @param {string} texto */
function especialesEn(texto) {
  return ESPECIALES.filter((e) => new RegExp(`\\b${e}\\b`).test(texto));
}

/**
 * @typedef {{
 *   metodo: string, ruta: string, archivo: string, linea: number,
 *   gates: Gate[], especiales: string[], dinamico: boolean, routerDesconocido: boolean,
 * }} RutaBackend
 */

/**
 * Todas las rutas declaradas en un archivo de router.
 * @param {string} fuente texto del `.py`
 * @param {string} archivo etiqueta para los mensajes
 * @returns {RutaBackend[]}
 */
export function extraerRutasFastapi(fuente, archivo) {
  const src = limpiarPython(fuente);
  const lineaDe = (/** @type {number} */ idx) => src.slice(0, idx).split("\n").length;

  /** @type {Record<string, number>} */
  const constantes = {};
  for (const m of src.matchAll(/^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(\d+)\s*$/gm))
    constantes[m[1]] = Number(m[2]);

  /** @type {Record<string, { prefix: string, gates: Gate[], especiales: string[] }>} */
  const routers = {};
  for (const m of src.matchAll(/^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*APIRouter\(/gm)) {
    const abre = (m.index ?? 0) + m[0].length - 1;
    const cierra = cerrarParentesis(src, abre);
    if (cierra === -1) continue;
    const args = src.slice(abre + 1, cierra);
    const prefix = args.match(/\bprefix\s*=\s*["']([^"']*)["']/);
    routers[m[1]] = {
      prefix: prefix ? prefix[1] : "",
      gates: gatesEn(args, constantes, `router ${m[1]}`),
      especiales: especialesEn(args),
    };
  }

  /** @type {RutaBackend[]} */
  const rutas = [];
  const dec = new RegExp(`^@([A-Za-z_][A-Za-z0-9_]*)\\.(${VERBOS.join("|")})\\(`, "gm");
  for (const m of src.matchAll(dec)) {
    const idx = m.index ?? 0;
    const abre = idx + m[0].length - 1;
    const cierra = cerrarParentesis(src, abre);
    if (cierra === -1) continue;
    const argsDec = src.slice(abre + 1, cierra);
    const ruta = argsDec.match(/^\s*["']([^"']*)["']/);
    if (!ruta) continue;

    // Decoradores adicionales hasta el `def`.
    let pos = cierra + 1;
    for (;;) {
      const resto = src.slice(pos).match(/^\s*@[A-Za-z_][A-Za-z0-9_.]*/);
      if (!resto) break;
      const p = pos + resto[0].length;
      pos = src[p] === "(" ? cerrarParentesis(src, p) + 1 : p;
      if (pos === 0) break;
    }
    const def = src.slice(pos).match(/^\s*(?:async\s+)?def\s+[A-Za-z_][A-Za-z0-9_]*\s*\(/);
    let argsFirma = "";
    let finFirma = pos;
    if (def) {
      const aF = pos + def[0].length - 1;
      const cF = cerrarParentesis(src, aF);
      if (cF !== -1) {
        argsFirma = src.slice(aF + 1, cF);
        finFirma = cF + 1;
      }
    }
    const resto = src.slice(finFirma);
    const sig = resto.search(/\n(?=@|def |async def |class )/);
    const cuerpo = sig === -1 ? resto : resto.slice(0, sig);

    const r = routers[m[1]];
    const nombre = m[1];
    const cabeza = `${nombre}.${m[2]}`;
    rutas.push({
      metodo: m[2].toUpperCase(),
      ruta: normalizarRuta(`${r?.prefix ?? ""}${ruta[1]}`),
      archivo,
      linea: lineaDe(idx),
      gates: [
        ...(r?.gates ?? []),
        ...gatesEn(argsDec, constantes, `decorador ${cabeza}`),
        ...gatesEn(argsFirma, constantes, `firma ${cabeza}`),
      ],
      especiales: [
        ...new Set([
          ...(r?.especiales ?? []),
          ...especialesEn(argsDec),
          ...especialesEn(argsFirma),
        ]),
      ],
      dinamico: /\bcheck_permission\(/.test(cuerpo),
      routerDesconocido: !r,
    });
  }
  return rutas;
}

/**
 * Busca la ruta de backend que atiende `metodo ruta`. Segmento a segmento: un
 * literal del front solo casa con el MISMO literal o con un parámetro del back;
 * un parámetro (`{}`) del front solo casa con un parámetro (no puede asumir que
 * `/trd/{}` sea `/trd/vencidos`). Gana la ruta con más literales coincidentes.
 * @param {RutaBackend[]} rutas
 * @param {string} metodo
 * @param {string} ruta ruta del front normalizada, sin `/api/v1`
 */
export function buscarRuta(rutas, metodo, ruta) {
  const objetivo = normalizarRuta(`/api/v1${ruta}`).split("/");
  /** @type {RutaBackend | null} */
  let mejor = null;
  let mejorPuntaje = -1;
  for (const r of rutas) {
    if (r.metodo !== metodo) continue;
    const seg = r.ruta.split("/");
    if (seg.length !== objetivo.length) continue;
    let puntaje = 0;
    let ok = true;
    for (let i = 0; i < seg.length && ok; i++) {
      if (seg[i] === objetivo[i]) puntaje += seg[i] === "{}" ? 0 : 1;
      else if (seg[i] === "{}" && objetivo[i] !== "{}") puntaje += 0;
      else ok = false;
    }
    if (ok && puntaje > mejorPuntaje) {
      mejor = r;
      mejorPuntaje = puntaje;
    }
  }
  return mejor;
}
