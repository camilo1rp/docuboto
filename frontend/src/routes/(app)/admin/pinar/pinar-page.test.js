// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * pinar-page.test.js — Render de /admin/pinar (+page.svelte, Fase 7).
 *
 * Cubre: gate del botón "Nuevo plan" por USUA_PERM_ADMIN, distinción entre
 * "sin planes" (vacío legítimo) y "no se pudo cargar" (loadError — nunca se
 * confunden), y que el listado pinta versión/estado/vigencia por fila.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen } from "@testing-library/svelte";
import { writable } from "svelte/store";

const gotoMock = vi.fn();
vi.mock("$app/navigation", () => ({ goto: gotoMock, invalidateAll: vi.fn() }));
vi.mock("$app/stores", () => {
  const page = writable({ url: new URL("http://localhost/admin/pinar") });
  return { page, navigating: writable(null), updated: writable(false) };
});

const { default: PinarPage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

function sessionWith({ permissions = [] } = {}) {
  return {
    sub: "u-1",
    email: "admin@orpyca.com",
    name: "Administradora",
    preferred_username: "administradora",
    roles: [],
    tenant_slug: "icetex",
    permissions,
    permission_levels: Object.fromEntries(permissions.map((p) => [p, 3])),
  };
}

const PLAN_1 = {
  id: "p-1",
  version: 1,
  estado: "borrador",
  vigencia_desde: "2026-01-01",
  vigencia_hasta: "2029-12-31",
  vision: null,
  acto_administrativo: null,
  aprobado_por: null,
  aprobado_at: null,
  created_at: "2026-01-01T10:00:00Z",
};

/** @param {Partial<import('./$types').PageData>} overrides */
function baseData(overrides = {}) {
  return { planes: [], loadError: null, estadoFiltro: "", ...overrides };
}

describe("/admin/pinar — gate del botón Nuevo plan por USUA_PERM_ADMIN", () => {
  beforeEach(() => {
    clearSession();
    gotoMock.mockClear();
  });
  afterEach(() => clearSession());

  it("sin USUA_PERM_ADMIN: no se ve el botón Nuevo plan", () => {
    initSession(sessionWith({ permissions: [] }));
    render(PinarPage, { props: { data: baseData({ planes: [PLAN_1] }) } });
    expect(screen.queryByRole("button", { name: "Nuevo plan" })).not.toBeInTheDocument();
  });

  it("con USUA_PERM_ADMIN: se ve el botón Nuevo plan", () => {
    initSession(sessionWith({ permissions: ["USUA_PERM_ADMIN"] }));
    render(PinarPage, { props: { data: baseData({ planes: [PLAN_1] }) } });
    expect(screen.getByRole("button", { name: "Nuevo plan" })).toBeInTheDocument();
  });
});

describe("/admin/pinar — estados de la vista", () => {
  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: ["USUA_PERM_ADMIN"] }));
  });
  afterEach(() => clearSession());

  it("vacío legítimo: 'Sin planes PINAR', SIN el texto de error de carga", () => {
    render(PinarPage, { props: { data: baseData() } });
    expect(screen.getByText("Sin planes PINAR")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("fallo de carga: banner de error explícito, nunca 'Sin planes PINAR'", () => {
    render(PinarPage, {
      props: { data: baseData({ loadError: "No se pudo conectar al servidor del PINAR." }) },
    });
    expect(screen.getByRole("alert")).toHaveTextContent(/no se pudo conectar/i);
    expect(screen.queryByText("Sin planes PINAR")).not.toBeInTheDocument();
  });

  it("con planes: pinta versión, estado y enlace 'Ver' al workspace", () => {
    render(PinarPage, { props: { data: baseData({ planes: [PLAN_1] }) } });
    expect(screen.getByText("v1")).toBeInTheDocument();
    // "Borrador" aparece tanto en el chip de estado de la fila como en la
    // opción del filtro — se acota al chip (span.op-status-chip).
    const chip = document.querySelector(".op-status-chip");
    expect(chip).toHaveTextContent("Borrador");
    // `Button` con `href` renderiza un <a> y se anuncia como ENLACE. Este
    // comentario decía lo contrario —«no role="link"»— y describía el defecto
    // como si fuera el contrato: el `role="button"` mentía sobre el teclado.
    const verLink = screen.getByRole("link", { name: /Ver/i });
    expect(verLink.getAttribute("href")).toBe("/admin/pinar/p-1");
  });
});
