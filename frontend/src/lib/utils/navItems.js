// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * navItems.js — Catálogo único de ítems de navegación + su filtro RBAC.
 *
 * Extraído de SidebarNav.svelte para que el módulo 04 "Favoritos" del
 * Dashboard (Sistema de Diseño §10) pueda resolver los mismos destinos
 * autorizados sin duplicar el catálogo NAV_ITEMS ni su lógica de gateo.
 * SidebarNav.svelte y el Dashboard consumen esta MISMA fuente — nunca
 * copiarla.
 *
 * - permission: se muestra si el usuario tiene ese permiso (string)
 * - permissions: se muestra si el usuario tiene AL MENOS UNO de esos permisos
 * - role: se muestra si el usuario tiene ese rol (string)
 * - Si no hay permission/permissions/role, el ítem es público (dashboard, etc.)
 */

import { ROUTES } from "./config.js";

export const NAV_ITEMS = [
  {
    label: "Panel principal",
    href: ROUTES.dashboard,
    icon: "fas fa-home",
  },
  {
    label: "Bandeja",
    href: ROUTES.bandeja,
    icon: "fas fa-inbox",
    // D-07: 'VER_BANDEJA' no existe en el catálogo RBAC (migración 004).
    // La bandeja es la pantalla base de cualquier operador que trabaja con radicados.
    // Se gatea por PERM_RADI (permiso base de radicación): todo operador activo lo tiene.
    permission: "PERM_RADI",
  },
  {
    label: "Radicar",
    href: ROUTES.radicar,
    icon: "fas fa-file-import",
    // La página permite PERM_RADI_ENTRADA || PERM_RADI_SALIDA || PERM_RADI_INTERNO (ADR-013)
    permissions: ["PERM_RADI_ENTRADA", "PERM_RADI_SALIDA", "PERM_RADI_INTERNO"],
  },
  {
    label: "Expedientes",
    href: ROUTES.expedientes,
    icon: "fas fa-folder-open",
    // D-07: 'VER_EXPEDIENTES' → USUA_PERM_EXPEDIENTE (migración 004)
    permission: "USUA_PERM_EXPEDIENTE",
  },
  {
    label: "Búsqueda",
    href: ROUTES.busqueda,
    icon: "fas fa-search",
    // D-07: 'BUSCAR_DOCUMENTOS' no existe en el catálogo RBAC.
    // La búsqueda es consulta de radicados; se gatea por PERM_RADI igual que la bandeja.
    permission: "PERM_RADI",
  },
  {
    label: "Reportes",
    href: ROUTES.reportes,
    icon: "fas fa-chart-bar",
    // D-07: 'VER_REPORTES' → SGD_PERM_ESTADISTICA (migración 004)
    permission: "SGD_PERM_ESTADISTICA",
  },
  {
    label: "Borradores",
    href: ROUTES.borradores,
    icon: "fas fa-file-alt",
    // PERM_RADI: mismo permiso que bandeja; todo operador que radica gestiona borradores.
    permission: "PERM_RADI",
  },
  {
    label: "Envíos",
    href: ROUTES.envios,
    icon: "fas fa-truck",
    // PERM_RADI_SALIDA: solo operadores de salida gestionan envíos postales (T-12e).
    permission: "PERM_RADI_SALIDA",
  },
  {
    label: "Firmas",
    href: ROUTES.firmas,
    icon: "fas fa-signature",
    // PERM_FIRMA: firmantes de la cadena de firma electrónica (T-12d, E06).
    permission: "PERM_FIRMA",
  },
  {
    label: "Resolver anulaciones",
    href: ROUTES.anulaciones,
    icon: "fas fa-ban",
    // PERM_PANU_CODI: aprobador de anulación de radicados — rol distinto del
    // solicitante (que usa PERM_SOL_ANULAR desde /bandeja). Cola de trabajo
    // propia (Fase 0, cierre de desfase API↔UI).
    permission: "PERM_PANU_CODI",
  },
  {
    label: "Archivo físico",
    href: ROUTES.archivoFisico,
    icon: "fas fa-box-archive",
    // USUA_PERM_EXPEDIENTE: gestión de expedientes/archivo (migración 004)
    permission: "USUA_PERM_EXPEDIENTE",
  },
  {
    label: "Transferencias",
    href: ROUTES.transferencias,
    icon: "fas fa-exchange-alt",
    permission: "USUA_PERM_EXPEDIENTE",
  },
  {
    label: "Administración",
    href: ROUTES.admin,
    icon: "fas fa-cog",
    role: "tenant_admin",
  },
  {
    label: "Plantillas",
    href: ROUTES.adminPlantillas,
    icon: "fas fa-copy",
    // USUA_PERM_ADMIN: gestión de plantillas de contenido (T-12c, D-11)
    permission: "USUA_PERM_ADMIN",
  },
];

/**
 * Filtra `NAV_ITEMS` (o cualquier catálogo con la misma forma) contra los
 * permisos/roles vigentes del JWT. Pura — sin acceso a stores — para que
 * SidebarNav y el Dashboard obtengan exactamente el mismo resultado a partir
 * de los mismos arrays de permisos/roles.
 *
 * @template {{permission?: string, permissions?: string[], role?: string}} T
 * @param {T[]} items
 * @param {string[]} permissions
 * @param {string[]} roles
 * @returns {T[]}
 */
export function filterVisibleNavItems(items, permissions, roles) {
  return items.filter((item) => {
    if (item.permissions) return item.permissions.some((p) => permissions.includes(p));
    if (item.permission) return permissions.includes(item.permission);
    if (item.role) return roles.includes(item.role);
    return true;
  });
}
