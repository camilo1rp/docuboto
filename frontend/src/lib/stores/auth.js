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

/**
 * Niveles CRUD por permiso (`GET /auth/me` → `permission_levels`, aditivo a
 * `permissions[]`). Escala de `auth_group_permissions.crud`: 0 ninguno ·
 * 1 leer · 2 editar · 3 crear/borrar · 4 nivel_1 · 5 nivel_2 (ROOT resuelve
 * `USUA_PERM_ROOT: 5`). Fail-closed por diseño del backend: sin contexto de
 * tenant o usuario no aprovisionado, es `{}` — nunca `null`/ausente.
 */
export const permissionLevels = derived(sessionUser, ($u) => $u?.permission_levels ?? {});

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
 * Los permisos (y sus niveles CRUD) se cargan de /auth/me tras el login.
 *
 * Sin `minCrud` (retrocompatible): solo mira presencia en `permissions[]` —
 * equivale a "el usuario tiene AL MENOS nivel 1 (leer)". Úsese para
 * condicionar navegación/visibilidad de secciones de solo lectura.
 *
 * Con `minCrud`: compara contra `permission_levels[permission]` (RF-SEG-03),
 * el mismo umbral que `require_permission(perm, min_crud=N)` exige en el
 * backend. Niveles: 0 ninguno · 1 leer · 2 editar · 3 crear/borrar ·
 * 4 nivel_1 · 5 nivel_2. Úsese SIEMPRE para gatear botones que disparan una
 * mutación (POST/PATCH/DELETE) — la UI debe ser espejo del RBAC real, no una
 * aproximación por presencia (ver hallazgo de revisión UX que motivó esto).
 *
 * Fail-closed explícito: si `permission_levels` no trae la clave (sesión
 * vieja emitida antes de este contrato, backend antiguo que aún no manda el
 * campo, o el usuario simplemente no tiene el permiso), el nivel efectivo es
 * `0` — nunca "no sé el nivel ⇒ lo permito".
 *
 * @param {string} permission — en SCREAMING_SNAKE_CASE (ej. 'PERM_RADI_ENTRADA')
 * @param {number} [minCrud] - umbral CRUD mínimo requerido por el backend
 * @returns {boolean}
 */
export function can(permission, minCrud) {
  if (minCrud === undefined) {
    return get(permissions).includes(permission);
  }
  const level = get(permissionLevels)[permission] ?? 0;
  return level >= minCrud;
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
 * Actualiza los permisos granulares (y sus niveles CRUD) tras cargar /auth/me.
 * @param {string[]} perms
 * @param {Record<string, number>} [levels] - `permission_levels` de /auth/me;
 *   se omite solo por retrocompat de tests/llamadas antiguas — el layout
 *   server SIEMPRE lo pasa (fail-closed: `{}` si /auth/me no lo trae).
 */
export function setPermissions(perms, levels) {
  sessionUser.update((u) =>
    u ? { ...u, permissions: perms, permission_levels: levels ?? {} } : u,
  );
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
 * @property {Record<string, number>} [permission_levels]
 *   Nivel CRUD por permiso (`GET /auth/me`, aditivo a `permissions[]`).
 *   Fail-closed: `{}` si el backend no lo trae (sesión vieja, /auth/me caído).
 * @property {{user_id: string, active_depe_id: number|null, available_depts: Array<object>}|null} [context]
 *   Contexto de dependencia activa (E08/ADR-013) — cargado por
 *   `(app)/+layout.server.js`, consumido por `DependencySwitcher.svelte`.
 * @property {boolean} [permisosDegradados]
 *   `true` cuando `GET /auth/me` no respondió y la sesión sigue viva pero sin
 *   permisos granulares. Sin este campo, el menú vacío es indistinguible de
 *   «me revocaron los permisos» — ver `(app)/+layout.server.js`.
 * @property {Array<{codigo: number, nombre: string, sigla: string}>} [dependencias]
 *   Catálogo mínimo de dependencias activas del tenant, para resolver la
 *   etiqueta legible de `context.active_depe_id`.
 */
