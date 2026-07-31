// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * bandeja-vista-switch.test.js — Conmutador de vista de /bandeja (T-Fase1).
 *
 * Cubre:
 *  - `vista` viene resuelta EN SSR (`data.vista`, ver +page.server.js) — el
 *    componente respeta lo que trae `data`, no decide por su cuenta.
 *  - vista="tablero" (default) monta el WorkflowBoard y NO el formulario de
 *    filtros de la vista Lista.
 *  - vista="lista" muestra el formulario de filtros/tabla clásica (T-08,
 *    preservada sin degradar) y NO el tablero de columnas.
 *  - La pestaña "Pendientes del equipo" solo aparece con USUA_PERM_ADMIN o
 *    PERM_TRAMITAR (espejo de RBAC).
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/svelte";
import { writable } from "svelte/store";

// `$app/navigation` y `$app/stores` se mockean porque el componente se monta
// aislado (sin el router real de SvelteKit) — mismo patrón que
// expedientes-page.test.js (revisión UX Fase 1, #1: sync de filtros con la URL).
vi.mock("$app/navigation", () => ({ goto: vi.fn() }));
vi.mock("$app/stores", () => {
  const page = writable({ url: new URL("http://localhost/bandeja") });
  return { page, navigating: writable(null), updated: writable(false) };
});

const { default: BandejaPage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

function baseData(overrides = {}) {
  return {
    documents: [],
    total: 0,
    loadError: null,
    vista: "tablero",
    dependencias: [],
    initialFilters: { docType: "", status: "", page: 1 },
    ...overrides,
  };
}

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
  clearSession();
  vi.restoreAllMocks();
});

function mockInboxFetch() {
  globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({ items: [], total: 0 })));
}

describe("/bandeja — conmutador de vista (?vista=)", () => {
  it("vista='tablero' (default) monta el tablero y no el formulario de filtros de Lista", async () => {
    mockInboxFetch();
    render(BandejaPage, { props: { data: baseData({ vista: "tablero" }) } });

    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(screen.queryByRole("search", { name: /filtrar radicados/i })).toBeNull();
  });

  it("vista='lista' muestra el formulario de filtros clásico y no dispara fetch de /inbox", async () => {
    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock;

    render(BandejaPage, { props: { data: baseData({ vista: "lista" }) } });

    expect(screen.getByRole("search", { name: /filtrar radicados/i })).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalledWith(
      expect.stringContaining("/bandeja/api/inbox"),
      expect.anything(),
    );
  });

  it("la pestaña 'Pendientes del equipo' está oculta sin PERM_TRAMITAR/USUA_PERM_ADMIN", () => {
    render(BandejaPage, { props: { data: baseData({ vista: "lista" }) } });
    expect(screen.queryByRole("link", { name: /pendientes del equipo/i })).toBeNull();
  });

  it("la pestaña 'Pendientes del equipo' aparece con PERM_TRAMITAR", () => {
    initSession({
      sub: "u-1",
      email: "operador@orpyca.com",
      name: "Operador",
      preferred_username: "operador",
      roles: [],
      tenant_slug: "icetex",
      permissions: ["PERM_TRAMITAR"],
    });
    render(BandejaPage, { props: { data: baseData({ vista: "lista" }) } });
    expect(screen.getByRole("link", { name: /pendientes del equipo/i })).toBeTruthy();
  });
});
