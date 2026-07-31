// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * auth.js — Store de sesión de usuario (lado cliente).
 *
 * Contiene los claims derivados del JWT que la UI necesita para:
 *  - mostrar nombre / email del usuario en la topbar
 *  - filtrar menú y acciones según roles y permisos (RBAC en UI)
 *  - propagar tenant_slug en apiFetch
 *
 * El store es SOLO lectura desde componentes; se inicializa desde
 * `(app)/+layout.svelte` usando los datos del layout server.
 *
 * Los tokens (access/refresh) NUNCA están en este store — viven en
 * cookies httpOnly gestionadas exclusivamente por el servidor SSR.
 *
 * Los tokens se leen desde las cookies httpOnly exclusivamente en el servidor.
 * Las páginas obtienen el token a través del evento `load` de SvelteKit
 * (+page.server.js / +layout.server.js) y lo pasan a api.service.js.
 * El cliente nunca accede directamente a los tokens.
 */

import { writable, derived, get } from "svelte/store";

// ---------------------------------------------------------------------------
// Estado de sesión
// ---------------------------------------------------------------------------

/** @type {import('svelte/store').Writable<SessionUser|null>} */
export const sessionUser = writable(null);

/** true mientras se verifica/carga la sesión inicial */
export const authLoading = writable(false);

// ---------------------------------------------------------------------------
// Stores derivados (acceso rápido en componentes y guardas)
// ---------------------------------------------------------------------------

/** Lista de roles realm del usuario actual */
export const roles = derived(sessionUser, ($u) => $u?.roles ?? []);

/** Lista de permisos granulares del usuario actual */
export const permissions = derived(sessionUser, ($u) => $u?.permissions ?? []);

/** Slug del tenant activo (del claim del JWT) */
export const tenantSlug = derived(sessionUser, ($u) => $u?.tenant_slug ?? null);

/** true si hay sesión activa */
export const isAuthenticated = derived(sessionUser, ($u) => $u !== null);

// ---------------------------------------------------------------------------
// Helpers de autorización (consumen los stores derivados)
// ---------------------------------------------------------------------------

/**
 * Devuelve true si el usuario tiene al menos uno de los roles indicados.
 * Usar para guardar de rutas y condicionar secciones de menú.
 *
 * @param {...string} requiredRoles
 * @returns {boolean}
 *
 * @example
 * if (hasRole('tenant_admin', 'superadmin')) { ... }
 */
export function hasRole(...requiredRoles) {
  const userRoles = get(roles);
  return requiredRoles.some((r) => userRoles.includes(r));
}

/**
 * Devuelve true si el usuario tiene el permiso granular indicado.
 * Los permisos se cargan de /auth/me tras el login.
 *
 * @param {string} permission — en SCREAMING_SNAKE_CASE (ej. 'PERM_RADI_ENTRADA')
 * @returns {boolean}
 */
export function can(permission) {
  return get(permissions).includes(permission);
}

// ---------------------------------------------------------------------------
// Inicialización desde el layout SSR (llamado en +layout.svelte)
// ---------------------------------------------------------------------------

/**
 * Inicializa el store de sesión con los datos que devuelve el layout server.
 * Se llama una vez al montar el layout raíz de la app.
 *
 * @param {SessionUser|null} user — datos del usuario del layout data
 */
export function initSession(user) {
  sessionUser.set(user);
}

/**
 * Actualiza los permisos granulares tras cargar /auth/me.
 * @param {string[]} perms
 */
export function setPermissions(perms) {
  sessionUser.update((u) => (u ? { ...u, permissions: perms } : u));
}

/**
 * Limpia la sesión (logout local, sin afectar la cookie — eso lo hace el servidor).
 */
export function clearSession() {
  sessionUser.set(null);
}

// ---------------------------------------------------------------------------
// JSDoc
// ---------------------------------------------------------------------------

/**
 * @typedef {object} SessionUser
 * @property {string} sub
 * @property {string|null} email
 * @property {string} name
 * @property {string|null} preferred_username
 * @property {string[]} roles
 * @property {string|null} tenant_slug
 * @property {string[]} permissions
 * @property {{user_id: string, active_depe_id: number|null, available_depts: Array<object>}|null} [context]
 *   Contexto de dependencia activa (E08/ADR-013) — cargado por
 *   `(app)/+layout.server.js`, consumido por `DependencySwitcher.svelte`.
 * @property {Array<{codigo: number, nombre: string, sigla: string}>} [dependencias]
 *   Catálogo mínimo de dependencias activas del tenant, para resolver la
 *   etiqueta legible de `context.active_depe_id`.
 */
