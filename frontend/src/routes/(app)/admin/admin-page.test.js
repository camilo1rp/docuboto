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

  // Al cerrar la Fase 5 ya no queda ninguna área con `implemented: false`: las
  // diez subpáginas existen. Este test cambió de premisa — antes comprobaba que
  // "Usuarios" se pintara deshabilitada; ahora comprueba lo contrario, porque
  // seguir anunciándola como "Próximamente" sería mentir sobre una pantalla que
  // ya está construida.
  //
  // No es un test que se limite a describir el presente: sirve de guarda. Si
  // alguien vuelve a añadir una tarjeta sin `href` (o marca una existente como
  // no implementada), reaparece el badge "Próximamente" y esto falla, obligando
  // a decidir conscientemente si el índice debe volver a anunciar un hueco.
  it("no queda ninguna área anunciada como pendiente: todas son enlaces navegables", () => {
    render(AdminPage);

    expect(screen.getByRole("link", { name: /^Usuarios/i })).toBeTruthy();
    expect(screen.queryAllByText("Próximamente")).toHaveLength(0);
    expect(document.querySelectorAll(".admin-card--disabled")).toHaveLength(0);
  });

  // El gate real de las tres colas de dead-letter es PERM_DLQ_ADMIN, no
  // USUA_PERM_ADMIN (ver `colas/+page.server.js`). Ofrecer la tarjeta a quien
  // no puede entrar produce un enlace que solo lleva a un 403 — el mismo modo
  // de fallo que ya se corrigió en el ítem "Administración" de `navItems.js`.
  it("la tarjeta de colas se rige por PERM_DLQ_ADMIN, no por USUA_PERM_ADMIN", () => {
    render(AdminPage);
    expect(screen.queryByText("Colas de errores")).toBeNull();
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
    ]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
  });
});

describe("/admin — TVD (ADR-026), gateada por USUA_PERM_TRD, no por USUA_PERM_ADMIN", () => {
  it("con solo USUA_PERM_ADMIN, la tarjeta TVD NO aparece", () => {
    clearSession();
    initSession(sessionWith({ permissions: ["USUA_PERM_ADMIN"] }));
    render(AdminPage);
    expect(screen.queryByText("TVD")).toBeNull();
  });

  it("con USUA_PERM_TRD, la tarjeta TVD es un enlace navegable a /admin/tvd", () => {
    clearSession();
    initSession(sessionWith({ permissions: ["USUA_PERM_TRD"] }));
    render(AdminPage);
    const tvdLink = screen.getByRole("link", { name: /^TVD/i });
    expect(tvdLink.getAttribute("href")).toBe("/admin/tvd");
  });
});

describe("/admin — Interoperabilidad (Fase 8), gateada por USUA_PERM_EXPEDIENTE, no por USUA_PERM_ADMIN", () => {
  it("con solo USUA_PERM_ADMIN, la tarjeta Interoperabilidad NO aparece", () => {
    clearSession();
    initSession(sessionWith({ permissions: ["USUA_PERM_ADMIN"] }));
    render(AdminPage);
    expect(screen.queryByText("Interoperabilidad")).toBeNull();
  });

  it("con USUA_PERM_EXPEDIENTE, la tarjeta es un enlace navegable a /admin/interoperabilidad", () => {
    clearSession();
    initSession(sessionWith({ permissions: ["USUA_PERM_EXPEDIENTE"] }));
    render(AdminPage);
    const link = screen.getByRole("link", { name: /^Interoperabilidad/i });
    expect(link.getAttribute("href")).toBe("/admin/interoperabilidad");
  });
});

describe("/admin — con USUA_PERM_ADMIN + PERM_DLQ_ADMIN", () => {
  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: ["USUA_PERM_ADMIN", "PERM_DLQ_ADMIN"] }));
  });

  it("con el permiso de colas, la tarjeta aparece y enlaza a /admin/colas", () => {
    render(AdminPage);
    const colasLink = screen.getByRole("link", { name: /Colas de errores/i });
    expect(colasLink.getAttribute("href")).toBe("/admin/colas");
  });
});
