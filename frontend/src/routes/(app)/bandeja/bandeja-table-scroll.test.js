// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * bandeja-table-scroll.test.js — revisión UX del cierre del desfase API↔UI,
 * lote 2, punto 2: la tabla de la vista Lista vivía en un contenedor con
 * `overflow: hidden` — en móvil las columnas Estado/Acciones se cortaban sin
 * barra de desplazamiento (WCAG 1.4.10) y, aunque desplazara, no tenía
 * `tabindex="0"` para usuarios de solo teclado (WCAG 2.1.1).
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen } from "@testing-library/svelte";
import { writable } from "svelte/store";

vi.mock("$app/navigation", () => ({ goto: vi.fn() }));
vi.mock("$app/stores", () => {
  const page = writable({ url: new URL("http://localhost/bandeja") });
  return { page, navigating: writable(null), updated: writable(false) };
});

const { default: BandejaPage } = await import("./+page.svelte");
const { clearSession } = await import("$lib/stores/auth.js");

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
  clearSession();
  vi.restoreAllMocks();
});

function baseData(overrides = {}) {
  return {
    documents: [
      {
        id: "d1",
        tracking_number: "2024-ICETEX-E-000001",
        doc_type: "entrada",
        subject: "Solicitud de prueba",
        target_depe_name: "Dependencia X",
        created_at: "2024-01-15T10:00:00Z",
        status: "distributed",
      },
    ],
    total: 1,
    loadError: null,
    vista: "lista",
    dependencias: [],
    initialFilters: { docType: "", status: "", page: 1 },
    ...overrides,
  };
}

describe("/bandeja — vista Lista, tabla desplazable con teclado", () => {
  it("el contenedor con scroll horizontal es alcanzable con teclado (tabindex=0)", () => {
    globalThis.fetch = vi.fn();
    render(BandejaPage, { props: { data: baseData() } });

    const scrollRegion = screen.getByRole("region", { name: /radicados.*desplazable/i });
    expect(scrollRegion).toHaveAttribute("tabindex", "0");
  });
});
