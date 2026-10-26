// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * bandeja-asignar-entry.test.js — Punto de entrada de "Asignar a flujo" (T-ASIGNAR).
 *
 * Cubre:
 *  - El botón "Asignar a flujo" vive en el cajón de detalle de la vista Lista,
 *    con el radicado ya en contexto (no un modal suelto con UUID a mano).
 *  - Solo se ofrece cuando el radicado NO tiene un paso activo
 *    (GET /bandeja/api/[id]/current → 404 no_active_step).
 *  - Si YA tiene un paso activo (200), no se ofrece "Asignar" — no hay una
 *    segunda vía distinta a Trasladar (Tablero) para el mismo radicado.
 *  - Está gateado por PERM_TRAMITAR (espejo de RBAC del backend).
 *  - Regresión: las tres vistas de /bandeja (tablero/lista/pendientes) siguen
 *    montando sin error.
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
const { initSession, clearSession } = await import("$lib/stores/auth.js");

const DOC = {
  id: "11111111-1111-1111-1111-111111111111",
  tracking_number: "2026-ICETEX-E-000001",
  doc_type: "E",
  subject: "Solicitud de certificación",
  status: "registered",
  registered_at: "2026-07-01T10:00:00Z",
  origin_dept: "Ventanilla Única",
  dest_dept: null,
  pages: 1,
};

function baseData(overrides = {}) {
  return {
    documents: [DOC],
    total: 1,
    loadError: null,
    vista: "lista",
    dependencias: [{ nombre: "Jurídica", sigla: "JUR" }],
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

function loginConTramitar() {
  initSession({
    sub: "u-1",
    user_id: "int-1",
    email: "operador@orpyca.com",
    name: "Operador",
    preferred_username: "operador",
    roles: [],
    tenant_slug: "icetex",
    permissions: ["PERM_TRAMITAR"],
  });
}

/** Enruta las llamadas que dispara openDrawer() para el radicado DOC. */
function mockDrawerFetch({ currentStatus, doc = { ...DOC, registered_by: "int-1" }, steps = [] }) {
  return vi.fn(async (url) => {
    const u = url.toString();
    if (u === `/bandeja/api/${DOC.id}`) return jsonResponse(doc);
    if (u === `/bandeja/api/${DOC.id}/history`) return jsonResponse({ steps });
    if (u.startsWith(`/anulaciones/api/${DOC.id}`)) return jsonResponse({}, 404);
    if (u === `/bandeja/api/${DOC.id}/current`) {
      return currentStatus === 200
        ? jsonResponse({ id: "step-1", radicado_id: DOC.id, to_dept: "Jurídica" }, 200)
        : jsonResponse({ error: "no_active_step" }, 404);
    }
    throw new Error(`fetch no esperado: ${u}`);
  });
}

describe("/bandeja — punto de entrada de Asignar (vista Lista, cajón de detalle)", () => {
  it("con PERM_TRAMITAR y sin paso activo (404), ofrece 'Asignar a flujo'", async () => {
    loginConTramitar();
    globalThis.fetch = mockDrawerFetch({ currentStatus: 404 });

    render(BandejaPage, { props: { data: baseData() } });
    await fireEvent.click(screen.getByRole("button", { name: /ver detalle/i }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /asignar a flujo/i })).toBeTruthy(),
    );
  });

  it("registered_by nulo (importado/ingesta): NO ofrece 'Asignar a flujo' (R16, fail-closed)", async () => {
    loginConTramitar();
    globalThis.fetch = mockDrawerFetch({
      currentStatus: 404,
      doc: { ...DOC, registered_by: null },
    });
    render(BandejaPage, { props: { data: baseData() } });
    await fireEvent.click(screen.getByRole("button", { name: /ver detalle/i }));
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 3, name: DOC.tracking_number })).toBeTruthy(),
    );
    await waitFor(() =>
      expect(globalThis.fetch).toHaveBeenCalledWith(`/bandeja/api/${DOC.id}/current`),
    );
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByRole("button", { name: /asignar a flujo/i })).toBeNull();
  });

  it("radicado de OTRA persona sin pasos: NO ofrece 'Asignar a flujo'", async () => {
    loginConTramitar();
    globalThis.fetch = mockDrawerFetch({
      currentStatus: 404,
      doc: { ...DOC, registered_by: "otro" },
    });
    render(BandejaPage, { props: { data: baseData() } });
    await fireEvent.click(screen.getByRole("button", { name: /ver detalle/i }));
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 3, name: DOC.tracking_number })).toBeTruthy(),
    );
    await waitFor(() =>
      expect(globalThis.fetch).toHaveBeenCalledWith(`/bandeja/api/${DOC.id}/history`),
    );
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByRole("button", { name: /asignar a flujo/i })).toBeNull();
  });

  it("con historial: quien radicó pero no poseía el último paso NO ve 'Asignar a flujo'", async () => {
    loginConTramitar();
    globalThis.fetch = mockDrawerFetch({
      currentStatus: 404,
      steps: [{ step_number: 1, status: "completed", assigned_to: "otro", to_dept: "Jurídica" }],
    });
    render(BandejaPage, {
      props: { data: baseData({ documents: [{ ...DOC, status: "distributed" }] }) },
    });
    await fireEvent.click(screen.getByRole("button", { name: /ver detalle/i }));
    await waitFor(() =>
      expect(globalThis.fetch).toHaveBeenCalledWith(`/bandeja/api/${DOC.id}/history`),
    );
    expect(screen.queryByRole("button", { name: /asignar a flujo/i })).toBeNull();
  });

  it("con paso activo (200), NO ofrece 'Asignar a flujo' — ya existe Trasladar en el Tablero", async () => {
    loginConTramitar();
    globalThis.fetch = mockDrawerFetch({ currentStatus: 200 });

    render(BandejaPage, { props: { data: baseData() } });
    await fireEvent.click(screen.getByRole("button", { name: /ver detalle/i }));

    // Esperar a que el detalle termine de cargar antes de afirmar la ausencia.
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 3, name: DOC.tracking_number })).toBeTruthy(),
    );
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /asignar a flujo/i })).toBeNull();
    });
  });

  it("sin PERM_TRAMITAR, no ofrece 'Asignar a flujo' aunque el radicado no tenga paso", async () => {
    // Sin sesión con permisos — espejo de RBAC en el front.
    globalThis.fetch = mockDrawerFetch({ currentStatus: 404 });

    render(BandejaPage, { props: { data: baseData() } });
    await fireEvent.click(screen.getByRole("button", { name: /ver detalle/i }));

    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 3, name: DOC.tracking_number })).toBeTruthy(),
    );
    expect(screen.queryByRole("button", { name: /asignar a flujo/i })).toBeNull();
  });
});

describe("/bandeja — regresión: las tres vistas siguen montando", () => {
  function mockInboxFetch() {
    return vi.fn(async () => jsonResponse({ items: [], total: 0 }));
  }

  it("vista='tablero' monta el WorkflowBoard sin error (tablero vacío → EmptyState)", async () => {
    globalThis.fetch = mockInboxFetch();
    render(BandejaPage, { props: { data: baseData({ vista: "tablero", documents: [] }) } });
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText(/sin pasos activos/i)).toBeTruthy());
  });

  it("vista='lista' monta la tabla clásica sin error", () => {
    globalThis.fetch = vi.fn();
    render(BandejaPage, { props: { data: baseData({ vista: "lista" }) } });
    expect(screen.getByText(DOC.tracking_number)).toBeTruthy();
  });

  it("vista='pendientes' (con PERM_TRAMITAR) monta el WorkflowBoard sin error", async () => {
    loginConTramitar();
    globalThis.fetch = mockInboxFetch();
    render(BandejaPage, { props: { data: baseData({ vista: "pendientes", documents: [] }) } });
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText(/sin pasos activos/i)).toBeTruthy());
  });
});
