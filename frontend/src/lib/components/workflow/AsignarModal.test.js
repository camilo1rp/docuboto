// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * AsignarModal.test.js — (T-ASIGNAR, RF-FLU-03).
 *
 * Cubre el rediseño del punto de entrada de "Asignar": el radicado llega por
 * prop (contexto de la fila/cajón donde se abrió el modal), no como campos de
 * identificador que el operador teclea o pega.
 *
 *  - `radicado_id`/`tracking_number` NUNCA son editables: el campo visible es
 *    de solo lectura y ya trae el número de radicado.
 *  - La dependencia destino es obligatoria: sin seleccionarla no se llama al proxy.
 *  - El payload de POST /bandeja/api/assign toma radicado_id/tracking_number/doc_type
 *    de la prop `radicado`, nunca de un input editado por el usuario.
 *  - Sin `radicado` (null), el botón de envío queda deshabilitado — no hay forma
 *    de enviar un asignar "vacío".
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import AsignarModal from "./AsignarModal.svelte";

const RADICADO = {
  id: "11111111-1111-1111-1111-111111111111",
  tracking_number: "2026-ICETEX-E-000001",
  doc_type: "E",
};

const DEPENDENCIAS = [
  { value: "7", label: "Jurídica" },
  { value: "12", label: "Archivo" },
];

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

describe("AsignarModal", () => {
  it("muestra el radicado recibido por prop en un campo deshabilitado (no editable)", () => {
    globalThis.fetch = vi.fn(async () => jsonResponse({ users: [] }));
    render(AsignarModal, { props: { open: true, radicado: RADICADO, dependencias: DEPENDENCIAS } });

    const campoRadicado = screen.getByLabelText(/^radicado$/i);
    expect(campoRadicado).toHaveValue("2026-ICETEX-E-000001");
    expect(campoRadicado).toBeDisabled();

    // No existen campos de identificador editables (UUID/tracking a mano).
    expect(screen.queryByLabelText(/identificador del radicado/i)).toBeNull();
    expect(screen.queryByLabelText(/número de radicado/i)).toBeNull();
  });

  it("sin radicado (null), el botón Asignar queda deshabilitado", () => {
    globalThis.fetch = vi.fn(async () => jsonResponse({ users: [] }));
    render(AsignarModal, { props: { open: true, radicado: null, dependencias: DEPENDENCIAS } });

    expect(screen.getByRole("button", { name: "Asignar" })).toBeDisabled();
  });

  it("no llama al proxy si no se selecciona dependencia destino", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ users: [] }));
    globalThis.fetch = fetchMock;

    render(AsignarModal, { props: { open: true, radicado: RADICADO, dependencias: DEPENDENCIAS } });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/bandeja/api/users?size=100"));

    await fireEvent.click(screen.getByRole("button", { name: "Asignar" }));

    expect(fetchMock).not.toHaveBeenCalledWith("/bandeja/api/assign", expect.anything());
    expect(screen.getByText(/la dependencia destino es obligatoria/i)).toBeTruthy();
  });

  it("POST /bandeja/api/assign con radicado_id/tracking_number/doc_type de la prop, no de un input", async () => {
    const fetchMock = vi.fn(async (url, opts) => {
      if (url === "/bandeja/api/users?size=100") return jsonResponse({ users: [] });
      expect(url).toBe("/bandeja/api/assign");
      const body = JSON.parse(opts.body);
      expect(body).toMatchObject({
        radicado_id: RADICADO.id,
        tracking_number: RADICADO.tracking_number,
        doc_type: "E",
        to_dept_code: 12,
      });
      expect(body.to_dept).toBeUndefined();
      return jsonResponse({ id: "step-1", radicado_id: RADICADO.id, to_dept: "Archivo" });
    });
    globalThis.fetch = fetchMock;

    render(AsignarModal, { props: { open: true, radicado: RADICADO, dependencias: DEPENDENCIAS } });

    const select = screen.getByLabelText(/dependencia destino/i);
    await fireEvent.change(select, { target: { value: "12" } });
    await fireEvent.click(screen.getByRole("button", { name: "Asignar" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/bandeja/api/assign", expect.any(Object));
    });
  });

  // I8 (revisión UX): un fallo al listar usuarios asignables se leía como
  // "no hay ningún responsable disponible" — sin ninguna señal de que la
  // carga falló. El operador debe poder seguir asignando (sin responsable),
  // pero la UI ya no debe mentir sobre el motivo.
  it("I8: un 500 al listar usuarios muestra un aviso junto al picker (la acción sigue siendo posible)", async () => {
    const fetchMock = vi.fn(async (url) => {
      if (url === "/bandeja/api/users?size=100") {
        return jsonResponse({ error: "internal_error" }, 500);
      }
      return jsonResponse({ id: "step-1" });
    });
    globalThis.fetch = fetchMock;

    render(AsignarModal, { props: { open: true, radicado: RADICADO, dependencias: DEPENDENCIAS } });

    await waitFor(() =>
      expect(screen.getByText(/no se pudo cargar la lista de responsables/i)).toBeTruthy(),
    );
    // La acción de asignar sigue disponible pese al fallo del picker.
    expect(screen.getByRole("button", { name: "Asignar" })).not.toBeDisabled();
  });

  // Menor (revisión UX): `due_at` se comparaba como instante exacto en el
  // backend (`_semaforo`, workflow_service.py) — con `T00:00:00` el plazo
  // expiraba en el primer instante del día elegido.
  it("envía due_at al final del día elegido (T23:59:59), no al inicio", async () => {
    const fetchMock = vi.fn(async (url, opts) => {
      if (url === "/bandeja/api/users?size=100") return jsonResponse({ users: [] });
      const body = JSON.parse(opts.body);
      expect(body.due_at).toBe(new Date("2026-08-15T23:59:59").toISOString());
      return jsonResponse({ id: "step-1" });
    });
    globalThis.fetch = fetchMock;

    render(AsignarModal, { props: { open: true, radicado: RADICADO, dependencias: DEPENDENCIAS } });

    const select = screen.getByLabelText(/dependencia destino/i);
    await fireEvent.change(select, { target: { value: "12" } });
    await fireEvent.change(screen.getByLabelText(/fecha límite/i), {
      target: { value: "2026-08-15" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Asignar" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/bandeja/api/assign", expect.any(Object));
    });
  });
});
