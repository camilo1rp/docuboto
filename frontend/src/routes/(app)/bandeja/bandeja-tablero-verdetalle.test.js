// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * bandeja-tablero-verdetalle.test.js — hallazgo A3 de la revisión UX: el
 * tablero no ofrecía forma de llegar al asunto de un radicado. "Ver detalle"
 * en una WorkflowCard debe abrir el mismo cajón que la vista Lista.
 *
 * Cubre también A6: el contador de la cabecera ("N radicados") solo describe
 * GET /documents — no debe mostrarse fuera de la vista Lista.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
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
const { clearSession } = await import("$lib/stores/auth.js");

const STEP = {
  id: "22222222-2222-2222-2222-222222222222",
  radicado_id: "11111111-1111-1111-1111-111111111111",
  tracking_number: "2026-ICETEX-E-000001",
  step_number: 1,
  from_dept: "Ventanilla Única",
  to_dept: "Jurídica",
  assigned_to: null,
  assigned_by: "u-1",
  action: "assign",
  notes: null,
  status: "pending",
  created_at: "2026-07-01T10:00:00Z",
  completed_at: null,
  doc_type: "E",
  due_at: null,
  semaforo: "verde",
};

const DOC_DETAIL = {
  id: STEP.radicado_id,
  tracking_number: STEP.tracking_number,
  doc_type: "E",
  subject: "Solicitud de certificación",
  status: "distributed",
  registered_at: "2026-07-01T10:00:00Z",
  origin_dept: "Ventanilla Única",
  dest_dept: "Jurídica",
  pages: 1,
};

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

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

function mockBoardAndDrawerFetch() {
  return vi.fn(async (url) => {
    const u = url.toString();
    if (u.startsWith("/bandeja/api/inbox")) return jsonResponse({ items: [STEP], total: 1 });
    if (u === `/bandeja/api/${STEP.radicado_id}`) return jsonResponse(DOC_DETAIL);
    if (u.startsWith(`/anulaciones/api/${STEP.radicado_id}`)) return jsonResponse({}, 404);
    if (u === `/bandeja/api/${STEP.radicado_id}/current`) {
      return jsonResponse({ error: "no_active_step" }, 404);
    }
    throw new Error(`fetch no esperado: ${u}`);
  });
}

describe("/bandeja — 'Ver detalle' desde el Tablero abre el cajón (A3, revisión UX)", () => {
  it("clic en 'Ver detalle' de una WorkflowCard abre el cajón con el asunto del radicado", async () => {
    globalThis.fetch = mockBoardAndDrawerFetch();

    render(BandejaPage, { props: { data: baseData({ vista: "tablero" }) } });

    await waitFor(() => expect(screen.getByText(STEP.tracking_number)).toBeTruthy());
    await fireEvent.click(screen.getByRole("button", { name: /ver detalle/i }));

    await waitFor(() => expect(screen.getByText("Solicitud de certificación")).toBeTruthy());
  });
});

describe("/bandeja — el contador de la cabecera solo aplica a la vista Lista (A6)", () => {
  it("en Tablero no muestra 'N radicados' (ese total describe GET /documents, no el tablero)", async () => {
    globalThis.fetch = vi.fn(async () => jsonResponse({ items: [], total: 0 }));
    render(BandejaPage, { props: { data: baseData({ vista: "tablero", total: 7 }) } });

    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(screen.queryByText(/^7 radicados$/)).toBeNull();
  });

  it("en Lista sí muestra el contador de radicados", () => {
    globalThis.fetch = vi.fn();
    render(BandejaPage, {
      props: {
        data: baseData({
          vista: "lista",
          total: 1,
          documents: [DOC_DETAIL],
        }),
      },
    });

    expect(screen.getByText(/1 radicado/)).toBeTruthy();
  });
});
