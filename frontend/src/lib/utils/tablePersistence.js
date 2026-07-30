// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * tablePersistence.js — Persistencia en `localStorage` de columnas visibles y
 * vistas guardadas de DataTable, espaciada por tenant + usuario + tabla.
 *
 * GUARDIA SSR (obligatoria, ver D-16/D-05): este front ya sufrió un 500 real
 * por tocar `localStorage`/`window` sin comprobar el entorno primero. Cada
 * función de este módulo comprueba `typeof window === "undefined"` de forma
 * DEFENSIVA e independiente de quién la llame — no basta con que el llamador
 * la invoque desde `onMount`, porque una función exportada puede reutilizarse
 * en el futuro desde un contexto que sí corre en SSR (p. ej. un bloque
 * reactivo). Ninguna función de aquí lanza si `window` no existe: son no-ops
 * que devuelven `null`/`false`.
 */

const NAMESPACE = "op-datatable";

/**
 * Construye la clave de almacenamiento espaciada por tenant + usuario + tabla.
 * `tenant`/`user` caen a "anon" cuando no hay sesión resuelta (p. ej. vistas
 * públicas) para no colisionar con `undefined`/`null` literales en la clave.
 * @param {object} params
 * @param {string|null|undefined} params.tenant
 * @param {string|null|undefined} params.user
 * @param {string} params.tableId
 * @param {"columns"|"views"} params.kind
 * @returns {string}
 */
export function buildStorageKey({ tenant, user, tableId, kind }) {
  const safeTenant = tenant || "anon";
  const safeUser = user || "anon";
  return `${NAMESPACE}:${safeTenant}:${safeUser}:${tableId}:${kind}`;
}

/**
 * Lee y parsea JSON de `localStorage`. `null` en SSR, ausencia de clave, o
 * JSON corrupto (nunca lanza — un valor corrupto no debe tumbar la tabla).
 * @param {string} key
 * @returns {unknown}
 */
export function readJson(key) {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(key);
    if (raw === null) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/**
 * Escribe JSON en `localStorage`. No-op silencioso en SSR o si el storage
 * está lleno/deshabilitado (modo privado de algunos navegadores).
 * @param {string} key
 * @param {unknown} value
 * @returns {boolean} true si se escribió
 */
export function writeJson(key, value) {
  if (typeof window === "undefined") return false;
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}

/**
 * Elimina una clave. No-op silencioso en SSR.
 * @param {string} key
 */
export function removeKey(key) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(key);
  } catch {
    // localStorage inaccesible (modo privado/cuota) — no es un error fatal.
  }
}
