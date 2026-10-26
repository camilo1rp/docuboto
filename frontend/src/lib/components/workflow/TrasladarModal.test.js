// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * TrasladarModal.test.js — (T-Fase1, RF-FLU-03).
 *
 * Cubre:
 *  - `from_dept` se deriva de `step.to_dept` y se muestra deshabilitado — el
 *    usuario NUNCA lo teclea (el backend responde 422 missing_from_dept si falta).
 *  - `to_dept` es obligatorio: sin seleccionarlo, no se llama al proxy.
 *  - El payload enviado a POST /bandeja/api/transfer trae radicado_id,
 *    tracking_number, to_dept y from_dept correctos.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import { readable } from "svelte/store";
import { NOMBRES_DEPENDENCIA_KEY } from "$lib/stores/dependencias.js";
import TrasladarModal from "./TrasladarModal.svelte";

const RAD_ID = "11111111-1111-1111-1111-111111111111";

const STEP = {
  id: "22222222-2222-2222-2222-222222222222",
  radicado_id: RAD_ID,
  tracking_number: "2026-ICETEX-E-000001",
  from_dept: "Ventanilla Única",
  to_dept: "Jurídica",
  to_dept_code: 7,
  doc_type: "E",
  status: "pending",
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

describe("TrasladarModal", () => {
  it("muestra la dependencia de origen derivada de step.to_dept, deshabilitada", () => {
    render(TrasladarModal, { props: { open: true, step: STEP, dependencias: DEPENDENCIAS } });
    const origen = screen.getByLabelText(/dependencia de origen/i);
    expect(origen).toHaveValue("Jurídica");
    expect(origen).toBeDisabled();
  });

  it("el origen se pinta con el nombre ACTUAL del catálogo, no con la instantánea (ADR-032)", () => {
    const context = new Map([
      [NOMBRES_DEPENDENCIA_KEY, readable(new Map([[7, "Gestión Jurídica"]]))],
    ]);
    render(TrasladarModal, {
      props: { open: true, step: STEP, dependencias: DEPENDENCIAS },
      context,
    });
    expect(screen.getByLabelText(/dependencia de origen/i)).toHaveValue("Gestión Jurídica");
  });

  it("no llama al proxy si no se selecciona dependencia destino", async () => {
    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock;

    render(TrasladarModal, { props: { open: true, step: STEP, dependencias: DEPENDENCIAS } });
    await fireEvent.click(screen.getByRole("button", { name: "Trasladar" }));

    expect(fetchMock).not.toHaveBeenCalledWith("/bandeja/api/transfer", expect.anything());
    expect(screen.getByText(/la dependencia destino es obligatoria/i)).toBeTruthy();
  });

  it("POST /bandeja/api/transfer con to_dept_code elegido y sin origen (lo deriva el servidor)", async () => {
    const fetchMock = vi.fn(async (url, opts) => {
      if (url === "/bandeja/api/users?size=100") return jsonResponse({ users: [] });
      expect(url).toBe("/bandeja/api/transfer");
      const body = JSON.parse(opts.body);
      expect(body).toMatchObject({
        radicado_id: RAD_ID,
        tracking_number: "2026-ICETEX-E-000001",
        to_dept_code: 12,
      });
      // ADR-032: el origen lo deriva el servidor; el front no lo envía.
      expect(body.from_dept).toBeUndefined();
      expect(body.from_dept_code).toBeUndefined();
      expect(body.to_dept).toBeUndefined();
      return jsonResponse({ ...STEP, to_dept: "Archivo" });
    });
    globalThis.fetch = fetchMock;

    render(TrasladarModal, { props: { open: true, step: STEP, dependencias: DEPENDENCIAS } });

    const select = screen.getByLabelText(/dependencia destino/i);
    await fireEvent.change(select, { target: { value: "12" } });
    await fireEvent.click(screen.getByRole("button", { name: "Trasladar" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/bandeja/api/transfer", expect.any(Object));
    });
  });

  // I8 (revisión UX): un fallo al listar usuarios asignables se leía como
  // "no hay ningún responsable disponible" — sin ninguna señal de que la
  // carga falló. El operador debe poder seguir trasladando (sin
  // responsable), pero la UI ya no debe mentir sobre el motivo.
  it("I8: un 503 al listar usuarios muestra un aviso junto al picker (la acción sigue siendo posible)", async () => {
    const fetchMock = vi.fn(async (url) => {
      if (url === "/bandeja/api/users?size=100") {
        return jsonResponse({ error: "connection_error" }, 503);
      }
      return jsonResponse({ ...STEP, to_dept: "Archivo" });
    });
    globalThis.fetch = fetchMock;

    render(TrasladarModal, { props: { open: true, step: STEP, dependencias: DEPENDENCIAS } });

    await waitFor(() =>
      expect(screen.getByText(/no se pudo cargar la lista de responsables/i)).toBeTruthy(),
    );
    expect(screen.getByRole("button", { name: "Trasladar" })).not.toBeDisabled();
  });

  // Menor (revisión UX): mismo criterio que AsignarModal — `due_at` se
  // comparaba como instante exacto en el backend (`_semaforo`,
  // workflow_service.py); `T00:00:00` marcaba el radicado "vencido" durante
  // toda la jornada de su propio plazo.
  it("envía due_at al final del día elegido (T23:59:59), no al inicio", async () => {
    const fetchMock = vi.fn(async (url, opts) => {
      if (url === "/bandeja/api/users?size=100") return jsonResponse({ users: [] });
      const body = JSON.parse(opts.body);
      expect(body.due_at).toBe(new Date("2026-08-15T23:59:59").toISOString());
      return jsonResponse({ ...STEP, to_dept: "Archivo" });
    });
    globalThis.fetch = fetchMock;

    render(TrasladarModal, { props: { open: true, step: STEP, dependencias: DEPENDENCIAS } });

    const select = screen.getByLabelText(/dependencia destino/i);
    await fireEvent.change(select, { target: { value: "12" } });
    await fireEvent.change(screen.getByLabelText(/fecha límite/i), {
      target: { value: "2026-08-15" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Trasladar" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/bandeja/api/transfer", expect.any(Object));
    });
  });
});
