// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin-page.test.js — Render de /admin (+page.svelte, Fase 0 cierre desfase
 * API↔UI: `navItems.js` ya enlazaba aquí pero la ruta era un 404 real).
 *
 * Cubre:
 *  - Sin ningún permiso de administración: EmptyState, ninguna tarjeta.
 *  - Con USUA_PERM_ADMIN: la tarjeta "Plantillas" es un enlace navegable;
 *    el resto de áreas (Usuarios, TRD/CCD, ...) se muestran DESHABILITADAS
 *    ("Próximamente") — no como enlaces muertos.
 *  - La distinción habilitada/deshabilitada no depende solo del color: la
 *    tarjeta deshabilitada lleva el badge de texto "Próximamente".
 */
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/svelte";

const { default: AdminPage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

function sessionWith({ permissions = [], roles = [] } = {}) {
  return {
    sub: "u-1",
    email: "admin@orpyca.com",
    name: "Administradora",
    preferred_username: "administradora",
    roles,
    tenant_slug: "icetex",
    permissions,
  };
}

describe("/admin — sin permisos de administración", () => {
  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: [] }));
  });

  it("muestra un EmptyState y ninguna tarjeta", () => {
    render(AdminPage);
    expect(screen.getByText(/sin permisos de administración/i)).toBeTruthy();
    expect(screen.queryByRole("link", { name: /Plantillas/i })).toBeNull();
    expect(screen.queryByRole("list", { name: /áreas de administración/i })).toBeNull();
  });
});

describe("/admin — con USUA_PERM_ADMIN", () => {
  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: ["USUA_PERM_ADMIN"] }));
  });

  it("un solo <h1> y la tarjeta Plantillas es un enlace a /admin/plantillas", () => {
    render(AdminPage);
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);

    const plantillasLink = screen.getByRole("link", { name: /Plantillas/i });
    expect(plantillasLink).toBeTruthy();
    expect(plantillasLink.getAttribute("href")).toBe("/admin/plantillas");
  });

  it("las áreas sin subruta implementada se muestran deshabilitadas, no como enlaces", () => {
    render(AdminPage);

    // "Usuarios" no tiene subruta implementada: no debe ser un <a> navegable.
    expect(screen.queryByRole("link", { name: /^Usuarios/i })).toBeNull();

    // Debe existir como tarjeta deshabilitada con marca de texto "Próximamente"
    // (no solo un cambio de color — regla de accesibilidad).
    const proximamenteBadges = screen.getAllByText("Próximamente");
    expect(proximamenteBadges.length).toBeGreaterThan(0);

    // M11 (revisión UX Fase 0): la tarjeta deshabilitada ya no lleva
    // `aria-disabled`/`aria-label` (rol implícito `generic`, ARIA 1.2 los
    // ignora) — la señal es el badge de texto + el icono/candado visibles.
    const usuariosLabel = screen.getByText("Usuarios");
    const disabledCard = usuariosLabel.closest(".admin-card--disabled");
    expect(disabledCard).toBeTruthy();
    expect(disabledCard.hasAttribute("aria-disabled")).toBe(false);
    expect(disabledCard.hasAttribute("aria-label")).toBe(false);
  });

  it("todas las áreas del catálogo aparecen (habilitadas o como mapa deshabilitado)", () => {
    render(AdminPage);
    for (const label of [
      "Plantillas",
      "Usuarios",
      "Grupos",
      "Dependencias",
      "TRD / CCD",
      "Metadatos",
      "Catálogos",
      "Parámetros",
      "Auditoría",
      "Notificaciones",
      "Colas de errores",
    ]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
  });
});
