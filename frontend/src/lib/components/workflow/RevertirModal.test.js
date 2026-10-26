// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * RevertirModal.test.js — (T-Fase1, RF-FLU-08).
 *
 * Cubre:
 *  - Doble confirmación: motivo obligatorio (≥5 caracteres) Y casilla marcada
 *    antes de que el botón "Revertir" quede habilitado / haga algo al pulsarse.
 *  - El botón que ejecuta usa la variante `danger`; "Cancelar" usa `ghost`.
 *  - POST /bandeja/api/{radicado_id}/rollback con `{ motivo }` en el body (el
 *    backend ahora exige motivo — ver bandeja/api/[id]/rollback/+server.js).
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import RevertirModal from "./RevertirModal.svelte";

const RAD_ID = "11111111-1111-1111-1111-111111111111";
const STEP = {
  id: "22222222-2222-2222-2222-222222222222",
  radicado_id: RAD_ID,
  tracking_number: "2026-ICETEX-E-000001",
  to_dept: "Jurídica",
  status: "pending",
};

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

describe("RevertirModal", () => {
  it("el botón Revertir usa la variante danger; Cancelar usa ghost", () => {
    render(RevertirModal, { props: { open: true, step: STEP } });
    expect(screen.getByRole("button", { name: /revertir/i }).className).toContain("op-btn--danger");
    expect(screen.getByRole("button", { name: "Cancelar" }).className).toContain("op-btn--ghost");
  });

  it("el botón Revertir está deshabilitado hasta marcar la casilla de confirmación", async () => {
    render(RevertirModal, { props: { open: true, step: STEP } });
    const boton = screen.getByRole("button", { name: /revertir/i });
    expect(boton).toBeDisabled();

    await fireEvent.click(screen.getByRole("checkbox"));
    expect(boton).not.toBeDisabled();
  });

  it("exige motivo de al menos 5 caracteres antes de llamar al proxy", async () => {
    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock;

    render(RevertirModal, { props: { open: true, step: STEP } });
    await fireEvent.click(screen.getByRole("checkbox"));
    await fireEvent.click(screen.getByRole("button", { name: /revertir/i }));

    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByText(/describa el motivo de la reversión/i)).toBeTruthy();
  });

  it("POST /bandeja/api/{radicado_id}/rollback con el motivo en el body cuando motivo y confirmación están completos", async () => {
    const fetchMock = vi.fn(async (url, opts) => {
      expect(url).toBe(`/bandeja/api/${RAD_ID}/rollback`);
      expect(opts.method).toBe("POST");
      expect(opts.headers).toEqual({ "Content-Type": "application/json" });
      expect(JSON.parse(opts.body)).toEqual({
        motivo: "Se asignó por error a la dependencia incorrecta.",
      });
      return jsonResponse({ ...STEP, status: "pending" });
    });
    globalThis.fetch = fetchMock;

    render(RevertirModal, { props: { open: true, step: STEP } });
    await fireEvent.input(screen.getByLabelText(/motivo de la reversión/i), {
      target: { value: "Se asignó por error a la dependencia incorrecta." },
    });
    await fireEvent.click(screen.getByRole("checkbox"));
    await fireEvent.click(screen.getByRole("button", { name: /revertir/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  });

  it("informa que el motivo queda registrado en la traza del radicado (no descarta la justificación)", () => {
    render(RevertirModal, { props: { open: true, step: STEP } });
    expect(screen.getByText(/queda registrado en la traza del radicado/i)).toBeTruthy();
  });

  it("la casilla de confirmación vive en un label con objetivo táctil ampliable (revisión UX Fase 1, #2)", () => {
    render(RevertirModal, { props: { open: true, step: STEP } });
    const checkbox = screen.getByRole("checkbox");
    expect(checkbox.closest("label")?.className).toContain("confirm-check");
  });

  it("recorta el motivo a 500 caracteres en el cliente (espejo del límite del backend)", async () => {
    render(RevertirModal, { props: { open: true, step: STEP } });
    await fireEvent.input(screen.getByLabelText(/motivo de la reversión/i), {
      target: { value: "a".repeat(600) },
    });
    expect(screen.getByLabelText(/motivo de la reversión/i).value).toHaveLength(500);
  });
});
