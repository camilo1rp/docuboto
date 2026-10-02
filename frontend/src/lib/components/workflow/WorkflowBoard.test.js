// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * WorkflowBoard.test.js — Tablero de distribución (T-Fase1, RF-FLU-03).
 *
 * Cubre lo que más importa de esta pantalla:
 *  - Fetch inicial a /bandeja/api/inbox (con `box` cuando se elige una pestaña).
 *  - EL BUG QUE QUEREMOS IMPOSIBILITAR: "Completar" llama a
 *    /bandeja/api/steps/{step.id}/complete — NUNCA con el radicado_id.
 *  - "Marcar en trámite" (equivalente de teclado del arrastre) llama al
 *    endpoint de transacciones con el radicado_id, tipo_tx=marcar_leido.
 *  - El arrastre de una tarjeta de Pendientes soltada en "En trámite" dispara
 *    la MISMA acción que el botón (misma función, mismo endpoint).
 *  - Cada tarjeta ofrece las 4 acciones como botones visibles (no solo un
 *    menú oculto) — ruta de teclado íntegra.
 */
import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/svelte";
import WorkflowBoard from "./WorkflowBoard.svelte";
import { initSession, clearSession } from "$lib/stores/auth.js";

// ADR-028: las acciones son del POSEEDOR. Sesión por defecto: soy YO y
// pertenezco a «Jurídica» (el to_dept de los pasos de prueba, sin dueño).
const YO = "aaaaaaaa-0000-0000-0000-000000000001";
const OTRO = "bbbbbbbb-0000-0000-0000-000000000002";
function login() {
  initSession({
    sub: "kc-sub",
    user_id: YO,
    email: "o@orpyca.com",
    name: "Op",
    preferred_username: "op",
    roles: [],
    tenant_slug: "icetex",
    permissions: ["PERM_TRAMITAR"],
    dependencias: [{ codigo: 7, nombre: "Jurídica", sigla: "JUR" }],
    context: { user_id: YO, active_depe_id: 7, available_depts: [{ depe_id: 7, is_active: true }] },
  });
}
beforeEach(login);

const STEP_ID = "22222222-2222-2222-2222-222222222222";
const RAD_ID = "11111111-1111-1111-1111-111111111111";

function step(overrides = {}) {
  return {
    id: STEP_ID,
    radicado_id: RAD_ID,
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
    ...overrides,
  };
}

function mockFetchRouter(routes) {
  return vi.fn(async (url, opts = {}) => {
    const u = typeof url === "string" ? url : url.toString();
    for (const [matcher, handler] of routes) {
      const match = typeof matcher === "string" ? u.startsWith(matcher) : matcher.test(u);
      if (match) return handler(u, opts);
    }
    throw new Error(`fetch no esperado: ${opts.method ?? "GET"} ${u}`);
  });
}

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

const originalFetch = globalThis.fetch;

afterEach(() => {
  clearSession();
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("WorkflowBoard — carga inicial", () => {
  it("consulta /bandeja/api/inbox y renderiza la tarjeta en la columna Pendientes", async () => {
    globalThis.fetch = mockFetchRouter([
      ["/bandeja/api/inbox", () => jsonResponse({ items: [step()], total: 1 })],
    ]);

    render(WorkflowBoard, { props: { source: "inbox", dependencias: [] } });

    await waitFor(() => expect(screen.getByText("2026-ICETEX-E-000001")).toBeTruthy());
    expect(screen.getByRole("heading", { name: /pendientes/i })).toBeTruthy();
    const calls = globalThis.fetch.mock.calls.map(([u]) => u.toString());
    expect(calls.some((u) => u.startsWith("/bandeja/api/inbox?"))).toBe(true);
  });

  it("al elegir la pestaña Entrada, agrega box=entrada a la consulta", async () => {
    globalThis.fetch = mockFetchRouter([
      ["/bandeja/api/inbox", () => jsonResponse({ items: [], total: 0 })],
    ]);

    render(WorkflowBoard, { props: { source: "inbox", dependencias: [] } });
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());

    await fireEvent.click(screen.getByRole("button", { name: "Entrada" }));

    await waitFor(() => {
      const calls = globalThis.fetch.mock.calls.map(([u]) => u.toString());
      expect(calls.some((u) => u.includes("box=entrada"))).toBe(true);
    });
  });

  it("fuente 'pending' consulta /bandeja/api/pending, no /inbox", async () => {
    globalThis.fetch = mockFetchRouter([
      ["/bandeja/api/pending", () => jsonResponse({ items: [], total: 0 })],
    ]);

    render(WorkflowBoard, { props: { source: "pending", dependencias: [] } });

    await waitFor(() => {
      const calls = globalThis.fetch.mock.calls.map(([u]) => u.toString());
      expect(calls.some((u) => u.startsWith("/bandeja/api/pending"))).toBe(true);
      expect(calls.some((u) => u.startsWith("/bandeja/api/inbox"))).toBe(false);
    });
    // Sin filtro por tipo: /pending no soporta `box` (bandeja gerencial).
    expect(screen.queryByRole("button", { name: "Entrada" })).toBeNull();
  });
});

describe("WorkflowBoard — Completar usa step_id, nunca radicado_id", () => {
  it("PATCH /bandeja/api/steps/{step.id}/complete — la URL nunca contiene el radicado_id", async () => {
    const fetchMock = mockFetchRouter([
      ["/bandeja/api/inbox", () => jsonResponse({ items: [step()], total: 1 })],
      [
        new RegExp(`/bandeja/api/steps/${STEP_ID}/complete$`),
        (u, opts) => {
          expect(opts.method).toBe("PATCH");
          return jsonResponse({ ...step(), status: "completed" });
        },
      ],
    ]);
    globalThis.fetch = fetchMock;

    render(WorkflowBoard, { props: { source: "inbox", dependencias: [] } });
    await waitFor(() => expect(screen.getByText("2026-ICETEX-E-000001")).toBeTruthy());

    await fireEvent.click(screen.getByRole("button", { name: /completar/i }));

    await waitFor(() => {
      const calls = fetchMock.mock.calls.map(([u]) => u.toString());
      expect(calls.some((u) => u.includes(`/steps/${STEP_ID}/complete`))).toBe(true);
      // Nunca se llama a un endpoint /bandeja/api/{radicado_id}/complete.
      expect(calls.some((u) => u.includes(`/${RAD_ID}/complete`))).toBe(false);
    });
  });
});

describe("WorkflowBoard — I10 (revisión UX): una acción en vuelo bloquea TODAS las tarjetas visiblemente", () => {
  it("mientras 'Completar' de una tarjeta está en vuelo, las acciones de las DEMÁS tarjetas quedan deshabilitadas", async () => {
    const STEP_2_ID = "33333333-3333-3333-3333-333333333333";
    const step2 = step({ id: STEP_2_ID, tracking_number: "2026-ICETEX-E-000002" });

    /** @type {(value: Response) => void} */
    let resolveComplete;
    const completePromise = new Promise((resolve) => {
      resolveComplete = resolve;
    });

    globalThis.fetch = mockFetchRouter([
      ["/bandeja/api/inbox", () => jsonResponse({ items: [step(), step2], total: 2 })],
      [new RegExp(`/bandeja/api/steps/${STEP_ID}/complete$`), () => completePromise],
    ]);

    render(WorkflowBoard, { props: { source: "inbox", dependencias: [] } });
    await waitFor(() => expect(screen.getByText("2026-ICETEX-E-000002")).toBeTruthy());

    const cards = screen.getAllByRole("button", { name: /completar/i });
    expect(cards).toHaveLength(2);

    await fireEvent.click(cards[0]);

    // Mientras la primera tarjeta espera respuesta, la SEGUNDA (no clicada)
    // también debe quedar deshabilitada — antes no ocurría nada visible.
    await waitFor(() => expect(cards[1]).toBeDisabled());
    const trasladarBtns = screen.getAllByRole("button", { name: /trasladar/i });
    expect(trasladarBtns[1]).toBeDisabled();

    resolveComplete(jsonResponse({ ...step(), status: "completed" }));
  });
});

describe("WorkflowBoard — Marcar en trámite (botón y arrastre, misma acción)", () => {
  it("botón 'En trámite' ejecuta la transacción marcar_leido sobre el radicado_id", async () => {
    const fetchMock = mockFetchRouter([
      ["/bandeja/api/inbox", () => jsonResponse({ items: [step()], total: 1 })],
      [
        new RegExp(`/bandeja/api/${RAD_ID}/transactions$`),
        async (u, opts) => {
          const body = JSON.parse(opts.body);
          expect(body).toEqual({ tipo_tx: "marcar_leido" });
          return jsonResponse({ id: "evt-1", tipo_tx: "marcar_leido" });
        },
      ],
    ]);
    globalThis.fetch = fetchMock;

    render(WorkflowBoard, { props: { source: "inbox", dependencias: [] } });
    await waitFor(() => expect(screen.getByText("2026-ICETEX-E-000001")).toBeTruthy());

    await fireEvent.click(screen.getByRole("button", { name: /en trámite/i }));

    await waitFor(() => {
      const calls = fetchMock.mock.calls.map(([u]) => u.toString());
      expect(calls.some((u) => u.includes(`/${RAD_ID}/transactions`))).toBe(true);
    });
  });

  it("soltar una tarjeta Pendiente sobre la columna En trámite dispara la misma acción", async () => {
    const fetchMock = mockFetchRouter([
      ["/bandeja/api/inbox", () => jsonResponse({ items: [step()], total: 1 })],
      [
        new RegExp(`/bandeja/api/${RAD_ID}/transactions$`),
        async (u, opts) => {
          const body = JSON.parse(opts.body);
          expect(body).toEqual({ tipo_tx: "marcar_leido" });
          return jsonResponse({ id: "evt-1", tipo_tx: "marcar_leido" });
        },
      ],
    ]);
    globalThis.fetch = fetchMock;

    render(WorkflowBoard, { props: { source: "inbox", dependencias: [] } });
    await waitFor(() => expect(screen.getByText("2026-ICETEX-E-000001")).toBeTruthy());

    const card = screen.getByText("2026-ICETEX-E-000001").closest("li");
    const enTramiteHeading = screen.getByRole("heading", { name: /en trámite/i });
    const dropzone = enTramiteHeading.closest("section");

    await fireEvent.dragStart(card);
    await fireEvent.drop(dropzone);

    await waitFor(() => {
      const calls = fetchMock.mock.calls.map(([u]) => u.toString());
      expect(calls.some((u) => u.includes(`/${RAD_ID}/transactions`))).toBe(true);
    });
  });
});

describe("WorkflowBoard — todas las acciones son botones visibles (paridad de teclado)", () => {
  it("cada tarjeta expone Trasladar, Trámite, Completar y Revertir como botones", async () => {
    globalThis.fetch = mockFetchRouter([
      [
        "/bandeja/api/inbox",
        () => jsonResponse({ items: [step({ status: "pending" })], total: 1 }),
      ],
    ]);

    render(WorkflowBoard, { props: { source: "inbox", dependencias: [] } });
    await waitFor(() => expect(screen.getByText("2026-ICETEX-E-000001")).toBeTruthy());

    const card = screen.getByText("2026-ICETEX-E-000001").closest("article");
    const scoped = within(card);
    expect(scoped.getByRole("button", { name: /trasladar/i })).toBeTruthy();
    expect(scoped.getByRole("button", { name: /^trámite$/i })).toBeTruthy();
    expect(scoped.getByRole("button", { name: /completar/i })).toBeTruthy();
    expect(scoped.getByRole("button", { name: /revertir/i })).toBeTruthy();
  });
});

describe("WorkflowBoard — Ver detalle (A3, revisión UX)", () => {
  it("hace burbujear el evento verDetalle con el step al hacer clic en 'Ver detalle'", async () => {
    globalThis.fetch = mockFetchRouter([
      ["/bandeja/api/inbox", () => jsonResponse({ items: [step()], total: 1 })],
    ]);

    const { component } = render(WorkflowBoard, { props: { source: "inbox", dependencias: [] } });
    await waitFor(() => expect(screen.getByText("2026-ICETEX-E-000001")).toBeTruthy());

    let detail = null;
    component.$on("verDetalle", (e) => {
      detail = e.detail;
    });
    await fireEvent.click(screen.getByRole("button", { name: /ver detalle/i }));

    expect(detail?.radicado_id).toBe(RAD_ID);
    expect(detail?.tracking_number).toBe("2026-ICETEX-E-000001");
  });
});

describe("WorkflowBoard — espejo de RBAC (canTramitar)", () => {
  it("con canTramitar=false, no ofrece Trasladar/Trámite/Completar/Revertir en ninguna tarjeta", async () => {
    globalThis.fetch = mockFetchRouter([
      ["/bandeja/api/inbox", () => jsonResponse({ items: [step()], total: 1 })],
    ]);

    render(WorkflowBoard, { props: { source: "inbox", dependencias: [], canTramitar: false } });
    await waitFor(() => expect(screen.getByText("2026-ICETEX-E-000001")).toBeTruthy());

    expect(screen.queryByRole("button", { name: /trasladar/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /^en trámite$/i })).toBeNull();
    // "Ver detalle" sigue disponible — es consulta, no ejecución.
    expect(screen.getByRole("button", { name: /ver detalle/i })).toBeTruthy();
  });
});

describe("WorkflowBoard — truncamiento sin paginación (A4, revisión UX)", () => {
  it("si total > tarjetas mostradas, avisa en vez de fingir que el tablero está completo", async () => {
    globalThis.fetch = mockFetchRouter([
      ["/bandeja/api/inbox", () => jsonResponse({ items: [step()], total: 230 })],
    ]);

    render(WorkflowBoard, { props: { source: "inbox", dependencias: [] } });
    await waitFor(() => expect(screen.getByText("2026-ICETEX-E-000001")).toBeTruthy());

    expect(screen.getByText(/mostrando los primeros 1 de 230/i)).toBeTruthy();
  });

  it("si total === tarjetas mostradas, no muestra el aviso de truncamiento", async () => {
    globalThis.fetch = mockFetchRouter([
      ["/bandeja/api/inbox", () => jsonResponse({ items: [step()], total: 1 })],
    ]);

    render(WorkflowBoard, { props: { source: "inbox", dependencias: [] } });
    await waitFor(() => expect(screen.getByText("2026-ICETEX-E-000001")).toBeTruthy());

    expect(screen.queryByText(/mostrando los primeros/i)).toBeNull();
  });
});

describe("WorkflowBoard — vacío con filtro activo no afirma ausencia general (A5, revisión UX)", () => {
  it("con box='entrada' y sin resultados, dice 'ningún radicado de tipo Entrada' y ofrece 'Ver todos'", async () => {
    const fetchMock = mockFetchRouter([
      [
        "/bandeja/api/inbox",
        (u) => {
          const isEntrada = u.includes("box=entrada");
          return jsonResponse({ items: isEntrada ? [] : [step()], total: isEntrada ? 0 : 1 });
        },
      ],
    ]);
    globalThis.fetch = fetchMock;

    render(WorkflowBoard, { props: { source: "inbox", dependencias: [] } });
    await waitFor(() => expect(screen.getByText("2026-ICETEX-E-000001")).toBeTruthy());

    await fireEvent.click(screen.getByRole("button", { name: "Entrada" }));

    await waitFor(() =>
      expect(screen.getByText(/ningún radicado de tipo entrada en esta bandeja/i)).toBeTruthy(),
    );
    expect(screen.queryByText(/no hay radicados asignados en esta bandeja/i)).toBeNull();
    expect(screen.getByRole("button", { name: /ver todos/i })).toBeTruthy();
  });

  it("fuente 'pending' vacía: 'no hay pasos pendientes visibles para tu nivel de acceso'", async () => {
    globalThis.fetch = mockFetchRouter([
      ["/bandeja/api/pending", () => jsonResponse({ items: [], total: 0 })],
    ]);

    render(WorkflowBoard, { props: { source: "pending", dependencias: [] } });

    await waitFor(() =>
      expect(
        screen.getByText(/no hay pasos pendientes visibles para tu nivel de acceso/i),
      ).toBeTruthy(),
    );
  });
});

describe("WorkflowBoard — hint de arrastre solo con puntero fino (revisión UX Fase 1, #3)", () => {
  it("con tarjetas en Pendientes y En trámite vacío, el hint existe pero en su propio nodo ocultable por CSS (pointer: coarse)", async () => {
    globalThis.fetch = mockFetchRouter([
      ["/bandeja/api/inbox", () => jsonResponse({ items: [step()], total: 1 })],
    ]);

    render(WorkflowBoard, { props: { source: "inbox", dependencias: [] } });
    await waitFor(() => expect(screen.getByText("2026-ICETEX-E-000001")).toBeTruthy());

    // El aviso "Sin radicados en trámite." siempre debe verse (arrastre o no);
    // el hint de arrastre es un <span> aparte para poder ocultarlo en táctil
    // sin dejar la columna sin ningún mensaje (@media pointer: coarse en <style>).
    expect(screen.getByText(/sin radicados en trámite\./i)).toBeTruthy();
    const hint = screen.getByText(/suelte aquí una tarjeta de pendientes para avanzarla/i);
    expect(hint.tagName).toBe("SPAN");
    expect(hint.className).toContain("wf-board__drag-hint");
  });
});

describe("WorkflowBoard — tenencia (ADR-028)", () => {
  const colega = () => step({ assigned_to: OTRO });

  it("paso asignado a un colega de mi dependencia: se ve pero no ofrece ninguna acción de ejecución", async () => {
    globalThis.fetch = mockFetchRouter([
      ["/bandeja/api/inbox", () => jsonResponse({ items: [colega()], total: 1 })],
    ]);
    render(WorkflowBoard, { props: { source: "inbox", dependencias: [] } });
    await waitFor(() => expect(screen.getByText("2026-ICETEX-E-000001")).toBeTruthy());
    for (const n of [/^en trámite$/i, /trasladar/i, /^trámite$/i, /completar/i, /revertir/i]) {
      expect(screen.queryByRole("button", { name: n })).toBeNull();
    }
    expect(screen.getByRole("button", { name: /ver detalle/i })).toBeTruthy();
  });

  it("paso mío: ofrece Marcar en trámite y las demás acciones", async () => {
    globalThis.fetch = mockFetchRouter([
      ["/bandeja/api/inbox", () => jsonResponse({ items: [step({ assigned_to: YO })], total: 1 })],
    ]);
    render(WorkflowBoard, { props: { source: "inbox", dependencias: [] } });
    await waitFor(() => expect(screen.getByRole("button", { name: /^en trámite$/i })).toBeTruthy());
    expect(screen.getByRole("button", { name: /completar/i })).toBeTruthy();
  });

  it("sin sesión (usuario desconocido): fail-closed, ninguna acción", async () => {
    clearSession();
    globalThis.fetch = mockFetchRouter([
      ["/bandeja/api/inbox", () => jsonResponse({ items: [step()], total: 1 })],
    ]);
    render(WorkflowBoard, { props: { source: "inbox", dependencias: [] } });
    await waitFor(() => expect(screen.getByText("2026-ICETEX-E-000001")).toBeTruthy());
    expect(screen.queryByRole("button", { name: /completar/i })).toBeNull();
  });

  it("arrastre: la tarjeta de un colega no es arrastrable y soltarla no llama al backend", async () => {
    const f = mockFetchRouter([
      ["/bandeja/api/inbox", () => jsonResponse({ items: [colega()], total: 1 })],
    ]);
    globalThis.fetch = f;
    const { container } = render(WorkflowBoard, { props: { source: "inbox", dependencias: [] } });
    await waitFor(() => expect(screen.getByText("2026-ICETEX-E-000001")).toBeTruthy());
    const li = /** @type {HTMLElement} */ (container.querySelector("li.wf-card"));
    expect(li.getAttribute("draggable")).toBe("false");
    const dt = { setData: vi.fn(), effectAllowed: "" };
    await fireEvent.dragStart(li, { dataTransfer: dt });
    const zona = /** @type {HTMLElement} */ (
      container.querySelector('[aria-labelledby="wf-col-en-tramite"]')
    );
    await fireEvent.drop(zona, { dataTransfer: dt });
    expect(f.mock.calls.some(([u]) => String(u).includes("/transactions"))).toBe(false);
  });

  it("arrastre: la tarjeta propia sí es arrastrable", async () => {
    globalThis.fetch = mockFetchRouter([
      ["/bandeja/api/inbox", () => jsonResponse({ items: [step({ assigned_to: YO })], total: 1 })],
    ]);
    const { container } = render(WorkflowBoard, { props: { source: "inbox", dependencias: [] } });
    await waitFor(() => expect(screen.getByText("2026-ICETEX-E-000001")).toBeTruthy());
    expect(container.querySelector("li.wf-card")?.getAttribute("draggable")).toBe("true");
  });
});
