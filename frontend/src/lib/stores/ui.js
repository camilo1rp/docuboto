// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * ui.js — Store de estado global de la interfaz.
 *
 * Gestiona:
 *  - sidebarCollapsed: el sidebar lateral está contraído (icono-only)
 *  - sidebarMobileOpen: en móvil el drawer lateral está abierto
 *  - activeModal: id del modal abierto actualmente (null = ninguno)
 *  - breadcrumbs: trail de migajas para BreadcrumbBar
 *
 * Ningún valor de negocio aquí — solo estado de presentación.
 */

import { writable, derived } from "svelte/store";

// ---------------------------------------------------------------------------
// Sidebar
// ---------------------------------------------------------------------------

/** @type {import('svelte/store').Writable<boolean>} */
export const sidebarCollapsed = writable(false);

/** @type {import('svelte/store').Writable<boolean>} */
export const sidebarMobileOpen = writable(false);

export function toggleSidebar() {
  sidebarCollapsed.update((v) => !v);
}

export function toggleMobileSidebar() {
  sidebarMobileOpen.update((v) => !v);
}

export function closeMobileSidebar() {
  sidebarMobileOpen.set(false);
}

// ---------------------------------------------------------------------------
// Modales
// ---------------------------------------------------------------------------

/** id del modal abierto, o null si ninguno */
export const activeModal = writable(/** @type {string|null} */ (null));

/** @param {string} id */
export function openModal(id) {
  activeModal.set(id);
}

export function closeModal() {
  activeModal.set(null);
}

/** @param {string} id */
export const isModalOpen = (id) => derived(activeModal, ($m) => $m === id);

// ---------------------------------------------------------------------------
// Breadcrumbs
// ---------------------------------------------------------------------------

/**
 * @typedef {{ label: string; href?: string }} Crumb
 */

/** @type {import('svelte/store').Writable<Crumb[]>} */
export const breadcrumbs = writable([]);

/** @param {Crumb[]} crumbs */
export function setBreadcrumbs(crumbs) {
  breadcrumbs.set(crumbs);
}
