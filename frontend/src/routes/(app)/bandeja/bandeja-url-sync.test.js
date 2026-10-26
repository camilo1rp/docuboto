// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * bandeja-url-sync.test.js — hallazgo #1 de la revisión UX Fase 1: los
 * filtros y la paginación de la vista Lista no viajaban en la URL, así que
 * no eran compartibles/bookmarkeables y se perdían al cambiar de pestaña
 * (Tablero → Lista) o al recargar.
 *
 * Cubre:
 *  - `applyFilters` escribe doc_type/status en la URL vía goto (replaceState).
 *  - `Limpiar filtros` quita esos params de la URL.
 *  - La paginación ("Página siguiente") escribe `page` en la URL.
 *  - Los href de las pestañas del conmutador de vista preservan los filtros
 *    vigentes (no los descartan al ir a Tablero y volver a Lista).
 *  - goto() se llama SIEMPRE con replaceState/keepFocus/noScroll (no debe
 *    llenar el historial ni robar foco/scroll).
 */
import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import { writable } from "svelte/store";

const gotoMock = vi.fn();
let currentPageStore;

vi.mock("$app/navigation", () => ({ goto: (...args) => gotoMock(...args) }));
vi.mock("$app/stores", () => {
  currentPageStore = writable({ url: new URL("http://localhost/bandeja?vista=lista") });
  return { page: currentPageStore, navigating: writable(null), updated: writable(false) };
});

const { default: BandejaPage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

function baseData(overrides = {}) {
  return {
    documents: [],
    total: 0,
    loadError: null,
    vista: "lista",
    dependencias: [],
    initialFilters: { docType: "", status: "", page: 1 },
    ...overrides,
  };
}

const originalFetch = globalThis.fetch;

beforeEach(() => {
  gotoMock.mockClear();
  currentPageStore.set({ url: new URL("http://localhost/bandeja?vista=lista") });
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  clearSession();
  vi.restoreAllMocks();
  gotoMock.mockClear();
});

function mockDocumentsFetch(total = 45) {
  globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({ documents: [], total })));
}

describe("/bandeja — filtros y paginación viajan en la URL (revisión UX Fase 1, #1)", () => {
  it("applyFilters escribe doc_type/status en la URL con replaceState (sin llenar el historial)", async () => {
    mockDocumentsFetch();
    render(BandejaPage, { props: { data: baseData() } });

    await fireEvent.change(screen.getByLabelText("Tipo"), { target: { value: "E" } });
    await fireEvent.change(screen.getByLabelText("Estado"), { target: { value: "registered" } });
    await fireEvent.click(screen.getByRole("button", { name: /filtrar/i }));

    await waitFor(() => expect(gotoMock).toHaveBeenCalled());
    const [url, opts] = gotoMock.mock.calls.at(-1);
    expect(url).toContain("doc_type=E");
    expect(url).toContain("status=registered");
    expect(opts).toMatchObject({ replaceState: true, keepFocus: true, noScroll: true });
  });

  it("Limpiar filtros quita doc_type/status de la URL", async () => {
    mockDocumentsFetch();
    render(BandejaPage, {
      props: {
        data: baseData({ initialFilters: { docType: "E", status: "registered", page: 1 } }),
      },
    });

    await fireEvent.click(screen.getByRole("button", { name: "Limpiar" }));

    await waitFor(() => expect(gotoMock).toHaveBeenCalled());
    const [url] = gotoMock.mock.calls.at(-1);
    expect(url).not.toContain("doc_type");
    expect(url).not.toContain("status");
  });

  it("la paginación escribe `page` en la URL al ir a la página siguiente", async () => {
    mockDocumentsFetch(45); // 45 / 20 = 3 páginas
    const doc = {
      id: "11111111-1111-1111-1111-111111111111",
      tracking_number: "2026-ICETEX-E-000001",
      doc_type: "E",
      subject: "Solicitud de certificación",
      status: "registered",
      registered_at: "2026-07-01T10:00:00Z",
      dest_dept: null,
    };
    render(BandejaPage, { props: { data: baseData({ documents: [doc], total: 45 }) } });

    const nextBtn = () => screen.getByRole("button", { name: "Página siguiente" });
    await waitFor(() => expect(nextBtn()).not.toBeDisabled());
    await fireEvent.click(nextBtn());

    await waitFor(() => expect(gotoMock).toHaveBeenCalled());
    const [url, opts] = gotoMock.mock.calls.at(-1);
    expect(url).toContain("page=2");
    expect(opts).toMatchObject({ replaceState: true, keepFocus: true, noScroll: true });
  });

  it("los href de las pestañas preservan doc_type/status/page vigentes (no los descarta al cambiar de vista)", () => {
    currentPageStore.set({
      url: new URL("http://localhost/bandeja?vista=lista&doc_type=E&status=registered&page=2"),
    });
    render(BandejaPage, {
      props: {
        data: baseData({
          vista: "lista",
          initialFilters: { docType: "E", status: "registered", page: 2 },
        }),
      },
    });

    const tabTablero = screen.getByRole("link", { name: /tablero/i });
    expect(tabTablero.getAttribute("href")).toContain("vista=tablero");
    expect(tabTablero.getAttribute("href")).toContain("doc_type=E");
    expect(tabTablero.getAttribute("href")).toContain("status=registered");
    expect(tabTablero.getAttribute("href")).toContain("page=2");
  });

  it("con permisos de gestión, el link a la pestaña Pendientes también preserva los filtros vigentes", () => {
    initSession({
      sub: "u-1",
      email: "operador@orpyca.com",
      name: "Operador",
      preferred_username: "operador",
      roles: [],
      tenant_slug: "icetex",
      permissions: ["PERM_TRAMITAR"],
    });
    currentPageStore.set({
      url: new URL("http://localhost/bandeja?vista=lista&doc_type=S"),
    });
    render(BandejaPage, {
      props: {
        data: baseData({ vista: "lista", initialFilters: { docType: "S", status: "", page: 1 } }),
      },
    });

    const tabPendientes = screen.getByRole("link", { name: /pendientes del equipo/i });
    expect(tabPendientes.getAttribute("href")).toContain("vista=pendientes");
    expect(tabPendientes.getAttribute("href")).toContain("doc_type=S");
  });
});
