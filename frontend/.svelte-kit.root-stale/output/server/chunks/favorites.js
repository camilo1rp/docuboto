import { R as ROUTES } from "./config.js";
import { w as writable } from "./index2.js";
const NAV_ITEMS = [
  {
    label: "Panel principal",
    href: ROUTES.dashboard,
    icon: "fas fa-home"
  },
  {
    label: "Bandeja",
    href: ROUTES.bandeja,
    icon: "fas fa-inbox",
    // D-07: 'VER_BANDEJA' no existe en el catálogo RBAC (migración 004).
    // La bandeja es la pantalla base de cualquier operador que trabaja con radicados.
    // Se gatea por PERM_RADI (permiso base de radicación): todo operador activo lo tiene.
    permission: "PERM_RADI"
  },
  {
    label: "Radicar",
    href: ROUTES.radicar,
    icon: "fas fa-file-import",
    // La página permite PERM_RADI_ENTRADA || PERM_RADI_SALIDA || PERM_RADI_INTERNO (ADR-013)
    permissions: ["PERM_RADI_ENTRADA", "PERM_RADI_SALIDA", "PERM_RADI_INTERNO"]
  },
  {
    label: "Expedientes",
    href: ROUTES.expedientes,
    icon: "fas fa-folder-open",
    // D-07: 'VER_EXPEDIENTES' → USUA_PERM_EXPEDIENTE (migración 004)
    permission: "USUA_PERM_EXPEDIENTE"
  },
  {
    label: "Búsqueda",
    href: ROUTES.busqueda,
    icon: "fas fa-search",
    // D-07: 'BUSCAR_DOCUMENTOS' no existe en el catálogo RBAC.
    // La búsqueda es consulta de radicados; se gatea por PERM_RADI igual que la bandeja.
    permission: "PERM_RADI"
  },
  {
    label: "Reportes",
    href: ROUTES.reportes,
    icon: "fas fa-chart-bar",
    // D-07: 'VER_REPORTES' → SGD_PERM_ESTADISTICA (migración 004)
    permission: "SGD_PERM_ESTADISTICA"
  },
  {
    label: "Borradores",
    href: ROUTES.borradores,
    icon: "fas fa-file-alt",
    // PERM_RADI: mismo permiso que bandeja; todo operador que radica gestiona borradores.
    permission: "PERM_RADI"
  },
  {
    label: "Envíos",
    href: ROUTES.envios,
    icon: "fas fa-truck",
    // PERM_RADI_SALIDA: solo operadores de salida gestionan envíos postales (T-12e).
    permission: "PERM_RADI_SALIDA"
  },
  {
    label: "Firmas",
    href: ROUTES.firmas,
    icon: "fas fa-signature",
    // PERM_FIRMA: firmantes de la cadena de firma electrónica (T-12d, E06).
    permission: "PERM_FIRMA"
  },
  {
    label: "Archivo físico",
    href: ROUTES.archivoFisico,
    icon: "fas fa-box-archive",
    // USUA_PERM_EXPEDIENTE: gestión de expedientes/archivo (migración 004)
    permission: "USUA_PERM_EXPEDIENTE"
  },
  {
    label: "Transferencias",
    href: ROUTES.transferencias,
    icon: "fas fa-exchange-alt",
    permission: "USUA_PERM_EXPEDIENTE"
  },
  {
    label: "Administración",
    href: ROUTES.admin,
    icon: "fas fa-cog",
    role: "tenant_admin"
  },
  {
    label: "Plantillas",
    href: ROUTES.adminPlantillas,
    icon: "fas fa-copy",
    // USUA_PERM_ADMIN: gestión de plantillas de contenido (T-12c, D-11)
    permission: "USUA_PERM_ADMIN"
  }
];
function filterVisibleNavItems(items, permissions, roles) {
  return items.filter((item) => {
    if (item.permissions) return item.permissions.some((p) => permissions.includes(p));
    if (item.permission) return permissions.includes(item.permission);
    if (item.role) return roles.includes(item.role);
    return true;
  });
}
const favoriteHrefs = writable([]);
function pickFavoriteNavItems(authorizedNavItems, hrefs) {
  return authorizedNavItems.filter((item) => hrefs.includes(item.href));
}
export {
  NAV_ITEMS as N,
  filterVisibleNavItems as a,
  favoriteHrefs as f,
  pickFavoriteNavItems as p
};
