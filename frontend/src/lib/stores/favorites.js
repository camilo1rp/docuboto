// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * favorites.js — Accesos de navegación fijados por el usuario (Sistema de
 * Diseño §10, módulo 04: "Accesos configurables por funcionalidad, definidos
 * por el usuario").
 *
 * El store SOLO guarda `href` (strings) — nunca copia label/icon/permiso al
 * fijar un ítem. Esto es deliberado: el destino visible SIEMPRE se resuelve
 * contra la lista de navegación vigente (`NAV_ITEMS`, permission-filtrada por
 * el JWT actual) en el momento de leer, nunca contra lo que había cuando el
 * usuario fijó el ítem. Un usuario puede perder un rol/permiso entre sesiones
 * — si guardáramos también el label/icono, un favorito huérfano podría
 * renderizarse igual con datos viejos. `pickFavoriteNavItems` exige la lista
 * de ítems ya autorizados como parámetro; jamás filtra por su cuenta.
 *
 * Consumidores previstos:
 *  - SidebarNav.svelte (sección "Favoritos" arriba del menú)
 *  - Dashboard (fase siguiente, accesos rápidos) — MISMA API, no duplicarla.
 *
 * GUARDIA SSR (D-16): reutiliza los helpers ya endurecidos de
 * `tablePersistence.js` (`readJson`/`writeJson`/`removeKey`), que son no-ops
 * seguros fuera del navegador. `initFavorites` además solo se debe llamar
 * desde `onMount`/bloques reactivos client-side de los componentes.
 */

import { writable } from "svelte/store";
import { readJson, writeJson, removeKey } from "$lib/utils/tablePersistence.js";

const NAMESPACE = "op-favorites";

/**
 * Construye la clave de almacenamiento espaciada por tenant + usuario, para
 * que dos usuarios (o dos tenants) en el mismo navegador nunca compartan ni
 * se pisen la lista de favoritos.
 * @param {string|null|undefined} tenant
 * @param {string|null|undefined} user
 * @returns {string}
 */
export function buildFavoritesKey(tenant, user) {
  const safeTenant = tenant || "anon";
  const safeUser = user || "anon";
  return `${NAMESPACE}:${safeTenant}:${safeUser}`;
}

/** @type {import('svelte/store').Writable<string[]>} */
export const favoriteHrefs = writable([]);

/** Clave de storage activa (namespaced); null hasta el primer `initFavorites`. */
let currentKey = /** @type {string|null} */ (null);

/**
 * Carga los favoritos persistidos del tenant/usuario indicados y los deja
 * activos para que `toggleFavorite` sepa dónde persistir. Llamar desde
 * `onMount` (o un bloque reactivo guardado con `browser`) cada vez que
 * cambie la sesión — nunca durante SSR.
 * @param {string|null|undefined} tenant
 * @param {string|null|undefined} user
 */
export function initFavorites(tenant, user) {
  currentKey = buildFavoritesKey(tenant, user);
  const stored = readJson(currentKey);
  favoriteHrefs.set(Array.isArray(stored) ? stored.filter((h) => typeof h === "string") : []);
}

/** Limpia el store en memoria (p. ej. al cerrar sesión) sin borrar lo persistido. */
export function resetFavorites() {
  currentKey = null;
  favoriteHrefs.set([]);
}

/**
 * Fija o desfija un `href` de navegación. No-op de persistencia si aún no se
 * llamó `initFavorites` (p. ej. render SSR) — el cambio en memoria sigue
 * aplicando para no romper la interacción en cliente.
 * @param {string} href
 */
export function toggleFavorite(href) {
  favoriteHrefs.update((current) => {
    const next = current.includes(href) ? current.filter((h) => h !== href) : [...current, href];
    if (currentKey) writeJson(currentKey, next);
    return next;
  });
}

/**
 * Elimina toda la lista de favoritos persistida para el tenant/usuario activo.
 */
export function clearPersistedFavorites() {
  if (currentKey) removeKey(currentKey);
  favoriteHrefs.set([]);
}

/**
 * Helper puro compartido por SidebarNav y el Dashboard: intersecta la lista
 * de `hrefs` fijados contra los ítems de navegación YA autorizados por el
 * RBAC vigente (permission-filtrados por el llamador). Preserva el orden de
 * `authorizedNavItems`, no el de `hrefs` — así un favorito reordena su
 * posición si cambia el orden canónico del menú, en vez de arrastrar un
 * orden arbitrario de fijado.
 *
 * REGLA DE SEGURIDAD: `authorizedNavItems` DEBE llegar ya filtrado por
 * roles/permisos del JWT actual. Esta función nunca vuelve a comprobar
 * permisos — si se le pasa la lista completa sin filtrar, expondría
 * destinos que el usuario ya no debería ver.
 *
 * @template {{href: string}} T
 * @param {T[]} authorizedNavItems
 * @param {string[]} hrefs
 * @returns {T[]}
 */
export function pickFavoriteNavItems(authorizedNavItems, hrefs) {
  return authorizedNavItems.filter((item) => hrefs.includes(item.href));
}
