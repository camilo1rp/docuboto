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
 *  - «Devolver al paso anterior» se corrige con otro traslado (ADR-057 §6): el
 *    texto no dice que sea irreversible y no hay casilla de «Entiendo…».
 *  - Motivo obligatorio (≥5 caracteres) antes de llamar al proxy.
 *  - El botón que ejecuta usa `primary`; "Cancelar" usa `ghost`.
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

describe("RevertirModal — Devolver al paso anterior (CR-001 UI-05)", () => {
  it("se titula «Devolver al paso anterior» y no afirma que sea irreversible", () => {
    render(RevertirModal, { props: { open: true, step: STEP } });
    expect(screen.getByRole("heading", { name: "Devolver al paso anterior" })).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/no se puede deshacer/i);
    expect(document.body.textContent).toMatch(/puede trasladarlo de nuevo/i);
  });

  it("no tiene casilla de confirmación: el motivo basta", () => {
    render(RevertirModal, { props: { open: true, step: STEP } });
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.getByRole("button", { name: /devolver/i }).hasAttribute("disabled")).toBe(false);
  });

  it("el botón Devolver no usa el rojo de lo definitivo; Cancelar usa ghost", () => {
    render(RevertirModal, { props: { open: true, step: STEP } });
    expect(screen.getByRole("button", { name: /devolver/i }).className).toContain(
      "op-btn--primary",
    );
    expect(screen.getByRole("button", { name: "Cancelar" }).className).toContain("op-btn--ghost");
  });

  it("exige motivo de al menos 5 caracteres antes de llamar al proxy", async () => {
    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock;

    render(RevertirModal, { props: { open: true, step: STEP } });
    await fireEvent.click(screen.getByRole("button", { name: /devolver/i }));

    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByText(/escriba el motivo de la devolución/i)).toBeTruthy();
  });

  it("POST /bandeja/api/{radicado_id}/rollback con el motivo en el body", async () => {
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
    await fireEvent.input(screen.getByLabelText(/motivo de la devolución/i), {
      target: { value: "Se asignó por error a la dependencia incorrecta." },
    });
    await fireEvent.click(screen.getByRole("button", { name: /devolver/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  });

  it("informa que el motivo queda registrado en la traza del radicado (no descarta la justificación)", () => {
    render(RevertirModal, { props: { open: true, step: STEP } });
    expect(screen.getByText(/queda registrado en la traza del radicado/i)).toBeTruthy();
  });

  it("recorta el motivo a 500 caracteres en el cliente (espejo del límite del backend)", async () => {
    render(RevertirModal, { props: { open: true, step: STEP } });
    await fireEvent.input(screen.getByLabelText(/motivo de la devolución/i), {
      target: { value: "a".repeat(600) },
    });
    expect(screen.getByLabelText(/motivo de la devolución/i).value).toHaveLength(500);
  });
});
