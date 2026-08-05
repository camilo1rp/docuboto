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
    label: "Mi perfil",
    href: ROUTES.perfil,
    icon: "fas fa-id-badge",
    // Sin `permission`/`role`: identidad, 2FA (TOTP) y credencial de firma
    // personal son propias de CUALQUIER usuario autenticado — los tres
    // routers de auth-service que consume (/me/totp, /me/signing-key,
    // /context) solo exigen `get_caller` (identidad propia), sin permiso
    // adicional (Fase 3, cierre de desfase API↔UI).
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
    // I11 (revisión UX): antes gateado por `role: "tenant_admin"`, un eje
    // INDEPENDIENTE del que aplica de verdad el backend. Todas las subrutas
    // reales (/admin/plantillas, /admin/flujos/reglas, /admin/flujos/reasignacion)
    // y el propio admin/+page.svelte exigen USUA_PERM_ADMIN — el rol Keycloak
    // `tenant_admin` no otorga ese permiso automáticamente (no hay mapeo
    // rol→permiso en auth-service, ADR-013: los permisos viven en
    // `auth_permissions`/asignación por usuario). Con el gate por rol, un
    // tenant_admin sin la asignación de permiso veía el enlace y aterrizaba en
    // "Sin permisos de administración" — enlace muerto. Unificado al mismo eje
    // que ya usa `admin/+page.svelte::ADMIN_AREAS`.
    permission: "USUA_PERM_ADMIN",
  },
  {
    label: "Plantillas",
    href: ROUTES.adminPlantillas,
    icon: "fas fa-copy",
    // USUA_PERM_ADMIN: gestión de plantillas de contenido (T-12c, D-11)
    permission: "USUA_PERM_ADMIN",
  },
  {
    label: "Flujos y reglas",
    href: ROUTES.adminFlujosReglas,
    icon: "fas fa-diagram-project",
    // USUA_PERM_ADMIN: administración de reglas de enrutamiento automático y
    // reasignación en cascada (E05, /admin/flujos).
    permission: "USUA_PERM_ADMIN",
  },
  {
    label: "Claves de firma",
    href: ROUTES.adminSeguridadClaves,
    icon: "fas fa-key",
    // USUA_PERM_ADMIN: gate REAL del backend en
    // `POST /auth/admin/users/{user_id}/signing-key/revoke`
    // (admin_router de services/auth-service/app/routers/signing_key.py,
    // `dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))]`
    // a nivel de router). Revocación administrativa de la credencial PKI
    // de OTRO usuario (Fase 3, cierre de desfase API↔UI).
    permission: "USUA_PERM_ADMIN",
  },
  {
    label: "Preservación",
    href: ROUTES.adminPreservacion,
    icon: "fas fa-shield-halved",
    // USUA_PERM_EXPEDIENTE: gate real de lectura del plan/pendientes de
    // preservación (storage-service `preservacion.py` + archive-service
    // `indice.py`); el reintento de sellado exige además PERM_FIRMA, pero la
    // pantalla se abre solo con USUA_PERM_EXPEDIENTE (E10, revisión UX
    // 2026-08-02, punto 3 del lote 1 — pantalla construida sin enlazar).
    permission: "USUA_PERM_EXPEDIENTE",
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
