// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * WorkflowCard.test.js — tarjeta de un paso de flujo (revisión UX, hallazgos
 * A3/A7/RBAC/estado-terminal).
 *
 * Cubre:
 *  - "Ver detalle" SIEMPRE visible y dispara el evento `verDetalle` (A3).
 *  - Con `canTramitar=false`, las acciones de ejecución se ocultan pero
 *    "Ver detalle" sigue disponible (espejo de RBAC).
 *  - Con un paso `completed`/`cancelled` (columna "Otros"), las acciones de
 *    ejecución se ocultan aunque `canTramitar` sea true (evitar 409).
 */
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/svelte";
import WorkflowCard from "./WorkflowCard.svelte";

function step(overrides = {}) {
  return {
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
    ...overrides,
  };
}

describe("WorkflowCard — Ver detalle (A3)", () => {
  it("siempre ofrece 'Ver detalle' y dispara verDetalle al hacer clic", async () => {
    const { component } = render(WorkflowCard, { props: { step: step() } });
    let detailFired = false;
    component.$on("verDetalle", () => {
      detailFired = true;
    });

    await fireEvent.click(screen.getByRole("button", { name: /ver detalle/i }));
    expect(detailFired).toBe(true);
  });

  it("'Ver detalle' sigue visible incluso sin canTramitar", () => {
    render(WorkflowCard, { props: { step: step(), canTramitar: false } });
    expect(screen.getByRole("button", { name: /ver detalle/i })).toBeTruthy();
  });
});

describe("WorkflowCard — espejo de RBAC (canTramitar)", () => {
  it("con canTramitar=false, oculta Trasladar/Trámite/Completar/Revertir", () => {
    const { container } = render(WorkflowCard, {
      props: { step: step({ status: "in_progress" }), canTramitar: false },
    });
    const scoped = within(container);
    expect(scoped.queryByRole("button", { name: /trasladar/i })).toBeNull();
    expect(scoped.queryByRole("button", { name: /trámite/i })).toBeNull();
    expect(scoped.queryByRole("button", { name: /completar/i })).toBeNull();
    expect(scoped.queryByRole("button", { name: /revertir/i })).toBeNull();
  });

  it("con canTramitar=true (default) y paso pending, ofrece las acciones de ejecución", () => {
    const { container } = render(WorkflowCard, { props: { step: step({ status: "pending" }) } });
    const scoped = within(container);
    expect(scoped.getByRole("button", { name: /en trámite/i })).toBeTruthy();
    expect(scoped.getByRole("button", { name: /trasladar/i })).toBeTruthy();
    expect(scoped.getByRole("button", { name: /revertir/i })).toBeTruthy();
  });
});

describe("WorkflowCard — pasos ya terminados (evitar 409)", () => {
  it("con status='completed', no ofrece acciones de ejecución aunque canTramitar sea true", () => {
    const { container } = render(WorkflowCard, {
      props: { step: step({ status: "completed" }), canTramitar: true },
    });
    const scoped = within(container);
    expect(scoped.queryByRole("button", { name: /trasladar/i })).toBeNull();
    expect(scoped.queryByRole("button", { name: /completar/i })).toBeNull();
    expect(scoped.queryByRole("button", { name: /revertir/i })).toBeNull();
    // "Ver detalle" sigue disponible: un paso terminado también se puede consultar.
    expect(scoped.getByRole("button", { name: /ver detalle/i })).toBeTruthy();
  });

  it("con status='cancelled', no ofrece acciones de ejecución", () => {
    const { container } = render(WorkflowCard, {
      props: { step: step({ status: "cancelled" }), canTramitar: true },
    });
    const scoped = within(container);
    expect(scoped.queryByRole("button", { name: /revertir/i })).toBeNull();
  });
});

describe("WorkflowCard — I10 (revisión UX): boardBusy deshabilita esta tarjeta aunque su propio busy sea false", () => {
  it("con boardBusy=true (otra tarjeta ocupada), deshabilita las acciones de ejecución de ESTA tarjeta", () => {
    const { container } = render(WorkflowCard, {
      props: { step: step({ status: "pending" }), busy: false, boardBusy: true },
    });
    const scoped = within(container);
    expect(scoped.getByRole("button", { name: /en trámite/i })).toBeDisabled();
    expect(scoped.getByRole("button", { name: /trasladar/i })).toBeDisabled();
    expect(scoped.getByRole("button", { name: "Trámite" })).toBeDisabled();
    expect(scoped.getByRole("button", { name: /completar/i })).toBeDisabled();
    expect(scoped.getByRole("button", { name: /revertir/i })).toBeDisabled();
    // "Ver detalle" es consulta, no acción de ejecución: nunca se bloquea.
    expect(scoped.getByRole("button", { name: /ver detalle/i })).not.toBeDisabled();
  });

  it("sin busy ni boardBusy, las acciones de ejecución están habilitadas", () => {
    const { container } = render(WorkflowCard, {
      props: { step: step({ status: "pending" }) },
    });
    const scoped = within(container);
    expect(scoped.getByRole("button", { name: /trasladar/i })).not.toBeDisabled();
  });
});

describe("WorkflowCard — tenencia (ADR-028)", () => {
  it("con poseedor=false oculta todas las acciones de ejecución (aunque canTramitar) y deja Detalle", () => {
    render(WorkflowCard, { props: { step: step(), canTramitar: true, poseedor: false } });
    for (const n of [/^en trámite$/i, /trasladar/i, /^trámite$/i, /completar/i, /revertir/i]) {
      expect(screen.queryByRole("button", { name: n })).toBeNull();
    }
    expect(screen.getByRole("button", { name: /detalle/i })).toBeTruthy();
  });
  it("Revertir solo con el paso pending (rollback, R19), no con in_progress", () => {
    render(WorkflowCard, { props: { step: step({ status: "in_progress" }), poseedor: true } });
    expect(screen.queryByRole("button", { name: /revertir/i })).toBeNull();
    expect(screen.getByRole("button", { name: /completar/i })).toBeTruthy();
  });

  it("con poseedor=true (default) las ofrece", () => {
    render(WorkflowCard, { props: { step: step(), poseedor: true } });
    expect(screen.getByRole("button", { name: /^en trámite$/i })).toBeTruthy();
  });
});
