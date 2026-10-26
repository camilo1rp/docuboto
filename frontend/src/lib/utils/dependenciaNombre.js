// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * dependenciaNombre.js — La dependencia se identifica por CÓDIGO y se muestra
 * por nombre (ADR-032). El nombre guardado en un paso, evento o radicado es la
 * INSTANTÁNEA de cuando ocurrió el acto; el nombre ACTUAL sale del catálogo.
 */

/**
 * Mapa código -> nombre actual a partir del catálogo de dependencias.
 * @param {Array<{codigo?: number|string, nombre?: string}>|null|undefined} catalogo
 * @returns {Map<number, string>}
 */
export function mapaNombres(catalogo) {
  /** @type {Map<number, string>} */
  const m = new Map();
  for (const d of catalogo ?? []) {
    const c = Number(d?.codigo);
    if (Number.isInteger(c) && c > 0 && typeof d?.nombre === "string" && d.nombre !== "") {
      m.set(c, d.nombre);
    }
  }
  return m;
}

/**
 * Nombre a pintar: el actual del catálogo; si el código no está (o no hay
 * catálogo), la instantánea; si tampoco, «—».
 * @param {number|string|null|undefined} codigo
 * @param {string|null|undefined} instantanea
 * @param {Map<number, string>|null|undefined} nombres
 */
export function nombreActual(codigo, instantanea, nombres) {
  const actual = codigo == null ? undefined : nombres?.get(Number(codigo));
  return actual ?? (instantanea || "—");
}

/**
 * Nombre actual SOLO si difiere de la instantánea (para «(hoy: …)»); si no
 * hay diferencia o no se puede saber, `null`.
 * @param {number|string|null|undefined} codigo
 * @param {string|null|undefined} instantanea
 * @param {Map<number, string>|null|undefined} nombres
 * @returns {string|null}
 */
export function nombreSiCambio(codigo, instantanea, nombres) {
  const actual = codigo == null ? undefined : nombres?.get(Number(codigo));
  if (!actual || !instantanea) return null;
  return actual.trim().toLowerCase() === instantanea.trim().toLowerCase() ? null : actual;
}

/**
 * Código de dependencia válido (entero 1..999999, como `ge=1, le=999999` del
 * backend) o `null`. Acepta número o cadena numérica (valor de un `<select>`).
 * @param {unknown} v
 * @returns {number|null}
 */
export function parseCodigoDependencia(v) {
  if (typeof v === "string" && !/^\d{1,6}$/.test(v.trim())) return null;
  if (typeof v !== "string" && typeof v !== "number") return null;
  const n = Number(v);
  return Number.isInteger(n) && n >= 1 && n <= 999999 ? n : null;
}

/**
 * Opciones de un selector de dependencia que NUNCA oculta el valor guardado:
 * las activas, más (si `valores` trae un código fuera de ellas) una opción
 * «(inactiva)» con el nombre del catálogo completo, o «Código N (no figura en
 * el catálogo)». Así un valor heredado se ve y se puede cambiar, en vez de
 * reenviarse a ciegas.
 * @param {Array<{value: string, label: string}>} activas
 * @param {Array<{codigo?: number|string, nombre?: string}>|null|undefined} todas
 * @param {Array<string|number|null|undefined>} valores
 */
export function opcionesConHuerfanas(activas, todas, valores) {
  const presentes = new Set(activas.map((o) => String(o.value)));
  const nombres = mapaNombres(todas);
  const extra = [];
  for (const v of valores) {
    const s = String(v ?? "").trim();
    if (!s || presentes.has(s)) continue;
    presentes.add(s);
    const nombre = nombres.get(Number(s));
    extra.push({
      value: s,
      label: nombre ? `${nombre} (inactiva)` : `Código ${s} (no figura en el catálogo)`,
    });
  }
  return [...extra, ...activas];
}
