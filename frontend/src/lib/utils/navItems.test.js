// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * navItems.test.js — Catálogo único de navegación + filtro RBAC puro.
 *
 * Cubre `filterVisibleNavItems`, la función que SidebarNav Y el Dashboard
 * (módulo 04 Favoritos, Sistema de Diseño §10) usan para resolver la MISMA
 * lista de destinos autorizados a partir del mismo catálogo NAV_ITEMS.
 */
import { describe, it, expect } from "vitest";
import { NAV_ITEMS, filterVisibleNavItems } from "./navItems.js";

describe("filterVisibleNavItems — espejo del RBAC", () => {
  it("sin permisos ni roles, solo deja los ítems públicos (sin permission/role)", () => {
    const visible = filterVisibleNavItems(NAV_ITEMS, [], []);
    expect(visible.every((item) => !item.permission && !item.permissions && !item.role)).toBe(true);
    expect(visible.map((i) => i.href)).toContain("/dashboard");
  });

  it("un ítem con `permission` se habilita solo si el usuario tiene ese permiso exacto", () => {
    const item = { label: "X", href: "/x", permission: "PERM_A" };
    expect(filterVisibleNavItems([item], ["PERM_B"], [])).toEqual([]);
    expect(filterVisibleNavItems([item], ["PERM_A"], [])).toEqual([item]);
  });

  it("un ítem con `permissions` (lista) se habilita con AL MENOS UNO de ellos", () => {
    const item = { label: "X", href: "/x", permissions: ["PERM_A", "PERM_B"] };
    expect(filterVisibleNavItems([item], ["PERM_B"], [])).toEqual([item]);
    expect(filterVisibleNavItems([item], ["PERM_C"], [])).toEqual([]);
  });

  it("un ítem con `role` se habilita solo por rol, nunca por permiso", () => {
    const item = { label: "X", href: "/x", role: "tenant_admin" };
    expect(filterVisibleNavItems([item], ["tenant_admin"], [])).toEqual([]);
    expect(filterVisibleNavItems([item], [], ["tenant_admin"])).toEqual([item]);
  });

  it("preserva el orden del catálogo de entrada", () => {
    const items = [
      { label: "A", href: "/a" },
      { label: "B", href: "/b" },
    ];
    expect(filterVisibleNavItems(items, [], []).map((i) => i.href)).toEqual(["/a", "/b"]);
  });

  it("NAV_ITEMS incluye el catálogo completo esperado por SidebarNav y el Dashboard", () => {
    const hrefs = NAV_ITEMS.map((i) => i.href);
    expect(hrefs).toEqual(
      expect.arrayContaining([
        "/dashboard",
        "/bandeja",
        "/radicar",
        "/expedientes",
        "/busqueda",
        "/reportes",
        "/borradores",
        "/envios",
        "/firmas",
        "/archivo-fisico",
        "/transferencias",
        "/admin",
        "/admin/plantillas",
      ]),
    );
  });
});
