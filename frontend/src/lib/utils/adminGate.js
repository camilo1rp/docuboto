// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * adminGate.js — Espejo UX del RBAC de las subrutas de /admin.
 *
 * Cada área declara el permiso que su router backend exige DE VERDAD (el mismo
 * que `ADMIN_AREAS` en `admin/+page.svelte`). Sirve para que forzar la URL de
 * un área sin permiso aterrice en /403 en vez de en una pantalla vacía de
 * errores 403 por XHR. NO es autorización: el backend re-valida cada llamada
 * (D-05); esto es UX y defensa en profundidad.
 *
 * Un área que no esté en el mapa exige USUA_PERM_ADMIN (fail-closed): añadir
 * una carpeta nueva bajo `admin/` sin declararla la deja cerrada, nunca abierta.
 */

/** Permiso por defecto de cualquier área no declarada. */
export const ADMIN_DEFAULT_PERMISSION = "USUA_PERM_ADMIN";

/**
 * Primer segmento tras `/admin/` → permisos de los que basta UNO.
 * @type {Record<string, string[]>}
 */
export const ADMIN_AREA_PERMISSIONS = {
  plantillas: ["USUA_PERM_ADMIN"],
  flujos: ["USUA_PERM_ADMIN"],
  usuarios: ["USUA_PERM_ADMIN"],
  grupos: ["USUA_PERM_ADMIN"],
  dependencias: ["USUA_PERM_ADMIN"],
  catalogos: ["USUA_PERM_ADMIN"],
  parametros: ["USUA_PERM_ADMIN"],
  auditoria: ["USUA_PERM_ADMIN"],
  notificaciones: ["USUA_PERM_ADMIN"],
  pinar: ["USUA_PERM_ADMIN"],
  correo: ["USUA_PERM_ADMIN"],
  seguridad: ["USUA_PERM_ADMIN"],
  trd: ["USUA_PERM_TRD"],
  tvd: ["USUA_PERM_TRD"],
  eliminacion: ["USUA_PERM_TRD"],
  metadatos: ["USUA_PERM_TRD"],
  terceros: ["PERM_RADI"],
  colas: ["PERM_DLQ_ADMIN"],
  interoperabilidad: ["USUA_PERM_EXPEDIENTE", "PERM_RADI"],
  preservacion: ["USUA_PERM_EXPEDIENTE"],
};

/**
 * Permisos de los que basta uno para entrar a `pathname`, o `null` si la ruta
 * es el índice `/admin` (que ya filtra sus tarjetas y muestra su EmptyState).
 * @param {string} pathname
 * @returns {string[] | null}
 */
export function permisosRequeridosAdmin(pathname) {
  const segmento = pathname.replace(/^\/admin\/?/, "").split("/")[0];
  if (!segmento) return null;
  return ADMIN_AREA_PERMISSIONS[segmento] ?? [ADMIN_DEFAULT_PERMISSION];
}
