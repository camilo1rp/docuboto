// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * ADR-029 — /reportes: `by_ubicacion` (dónde está lo que sigue en trámite) y
 * `by_dependencia` (de RADICACIÓN) son dos paneles distintos con claves distintas.
 * Las dos series difieren a propósito: con valores iguales no se sabría de cuál lee cada panel.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/svelte";
import { writable } from "svelte/store";

vi.mock("$app/navigation", () => ({ goto: vi.fn() }));
vi.mock("$app/environment", () => ({ browser: false, dev: false, building: false }));
vi.mock("$app/stores", () => ({
  page: writable({ url: new URL("http://localhost/reportes") }),
  navigating: writable(null),
  updated: writable(false),
}));

const { default: Reportes } = await import("./+page.svelte");

const SUMMARY = {
  total: 6,
  by_doc_type: { E: 6 },
  by_status: { in_progress: 5, closed: 1 },
  by_month: [{ month: "2026-08", count: 6 }],
  by_dependencia: { "Oficina de Tecnología": 6 },
  by_ubicacion: { "Control Interno": 3, "Talento Humano": 2 },
};

const data = (summary = SUMMARY) => ({
  summary,
  summaryError: null,
  indiceItems: [],
  indiceError: null,
  indiceTotal: 0,
  filtros: { dateFrom: "", dateTo: "" },
  puedeVerIndice: false,
});

describe("/reportes — ubicación actual vs dependencia de radicación", () => {
  it("hay un panel por cada una, con su propio encabezado", () => {
    render(Reportes, { data: data() });
    expect(screen.getByRole("heading", { level: 2, name: /Por ubicación actual/ })).toBeTruthy();
    expect(
      screen.getByRole("heading", { level: 2, name: "Por dependencia de radicación" }),
    ).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Por dependencia destino" })).toBeNull();
  });

  it("cada panel lee SU mapa: by_ubicacion no se mezcla con by_dependencia", () => {
    render(Reportes, { data: data() });
    const ubic = screen.getByRole("heading", { name: /Por ubicación actual/ }).closest("section");
    const rad = screen
      .getByRole("heading", { name: "Por dependencia de radicación" })
      .closest("section");
    expect(within(ubic).getByText("Control Interno")).toBeTruthy();
    expect(within(ubic).getByText("Talento Humano")).toBeTruthy();
    expect(within(ubic).queryByText("Oficina de Tecnología")).toBeNull();
    expect(within(rad).getByText("Oficina de Tecnología")).toBeTruthy();
    expect(within(rad).queryByText("Control Interno")).toBeNull();
  });

  it("el panel de ubicación aclara que excluye cerrados y anulados", () => {
    render(Reportes, { data: data() });
    expect(screen.getByText(/No incluye cerrados ni anulados/)).toBeTruthy();
  });

  it("un backend sin by_ubicacion no rompe la pantalla", () => {
    const { by_ubicacion, ...sin } = SUMMARY;
    expect(() => render(Reportes, { data: data(sin) })).not.toThrow();
  });
});
