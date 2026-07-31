// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * TransaccionModal.test.js — (T-Fase1, RF-FLU-01).
 *
 * Cubre A8 (revisión UX): un tipo de trámite cuyo `target_step_status` del
 * catálogo es `cancelled` (hoy solo `anular`, migración 007) no es un trámite
 * neutro más — exige describir el motivo y usa el botón de variante `danger`.
 * Un tipo neutro (`nrr`) sigue funcionando sin exigir comentario.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import TransaccionModal from "./TransaccionModal.svelte";

const RAD_ID = "11111111-1111-1111-1111-111111111111";

const STEP = {
  id: "22222222-2222-2222-2222-222222222222",
  radicado_id: RAD_ID,
  tracking_number: "2026-ICETEX-E-000001",
  to_dept: "Jurídica",
  status: "pending",
};

const CATALOG = [
  { code: "nrr", nombre: "No requiere respuesta", requires_permission: "PERM_TRAMITAR" },
  {
    code: "anular",
    nombre: "Anular",
    requires_permission: "PERM_ANULAR",
    target_step_status: "cancelled",
  },
];

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

describe("TransaccionModal — un trámite que cancela el paso (A8)", () => {
  it("con el catálogo cargado, seleccionar 'Anular' exige comentario antes de enviar", async () => {
    const fetchMock = vi.fn(async (url) => {
      if (url === "/bandeja/api/transaction-types") return jsonResponse(CATALOG);
      throw new Error(`fetch no esperado: ${url}`);
    });
    globalThis.fetch = fetchMock;

    render(TransaccionModal, { props: { open: true, step: STEP } });
    await waitFor(() => expect(screen.getByLabelText(/tipo de trámite/i)).toBeTruthy());

    const select = screen.getByLabelText(/tipo de trámite/i);
    await fireEvent.change(select, { target: { value: "anular" } });

    // Aviso explícito de lo que va a ocurrir.
    expect(screen.getByText(/cancelará el paso activo/i)).toBeTruthy();

    await fireEvent.click(screen.getByRole("button", { name: /ejecutar trámite/i }));

    expect(
      screen.getByText(/describa el motivo: este trámite cancela el paso activo/i),
    ).toBeTruthy();
    // No se llamó a POST /transactions sin el comentario.
    expect(fetchMock).not.toHaveBeenCalledWith(
      expect.stringContaining("/transactions"),
      expect.anything(),
    );
  });

  it("con comentario diligenciado, POST /transactions incluye tipo_tx y comentario", async () => {
    const fetchMock = vi.fn(async (url, opts) => {
      if (url === "/bandeja/api/transaction-types") return jsonResponse(CATALOG);
      expect(url).toBe(`/bandeja/api/${RAD_ID}/transactions`);
      const body = JSON.parse(opts.body);
      expect(body).toEqual({ tipo_tx: "anular", comentario: "Se asignó por error." });
      return jsonResponse({ id: "evt-1", tipo_tx: "anular" });
    });
    globalThis.fetch = fetchMock;

    render(TransaccionModal, { props: { open: true, step: STEP } });
    await waitFor(() => expect(screen.getByLabelText(/tipo de trámite/i)).toBeTruthy());

    await fireEvent.change(screen.getByLabelText(/tipo de trámite/i), {
      target: { value: "anular" },
    });
    await fireEvent.input(screen.getByLabelText(/motivo de la cancelación/i), {
      target: { value: "Se asignó por error." },
    });
    await fireEvent.click(screen.getByRole("button", { name: /ejecutar trámite/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        `/bandeja/api/${RAD_ID}/transactions`,
        expect.any(Object),
      );
    });
  });

  it("un tipo neutro (nrr) no exige comentario", async () => {
    const fetchMock = vi.fn(async (url, opts) => {
      if (url === "/bandeja/api/transaction-types") return jsonResponse(CATALOG);
      expect(url).toBe(`/bandeja/api/${RAD_ID}/transactions`);
      const body = JSON.parse(opts.body);
      expect(body).toEqual({ tipo_tx: "nrr" });
      return jsonResponse({ id: "evt-2", tipo_tx: "nrr" });
    });
    globalThis.fetch = fetchMock;

    render(TransaccionModal, { props: { open: true, step: STEP } });
    await waitFor(() => expect(screen.getByLabelText(/tipo de trámite/i)).toBeTruthy());

    await fireEvent.change(screen.getByLabelText(/tipo de trámite/i), { target: { value: "nrr" } });
    expect(screen.queryByText(/cancelará el paso activo/i)).toBeNull();

    await fireEvent.click(screen.getByRole("button", { name: /ejecutar trámite/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        `/bandeja/api/${RAD_ID}/transactions`,
        expect.any(Object),
      );
    });
  });
});
