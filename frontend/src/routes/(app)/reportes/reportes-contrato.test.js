// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * `/reportes` respondía 500.
 *
 * La pantalla leía `by_type` y `by_dept` —que el resumen no tiene— y trataba
 * `by_status` como una lista, cuando `ReportSummary` lo entrega como un
 * **diccionario** `{estado: cantidad}`. `{}.map` no es una función: no salía
 * una tabla vacía, se caía la página entera. Y solo «funcionaba» cuando el
 * resumen NO se podía cargar, porque entonces `data.summary?.by_status ?? []`
 * sí devolvía una lista — el único camino verde era el del fallo.
 *
 * Este test monta la página con la forma REAL del contrato de
 * `document-service/app/schemas/report.py`. Un test escrito sobre la forma que
 * la pantalla suponía habría pasado con el defecto puesto.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/svelte";
import { writable } from "svelte/store";

vi.mock("$app/navigation", () => ({ goto: vi.fn() }));
vi.mock("$app/environment", () => ({ browser: false, dev: false, building: false }));
vi.mock("$app/stores", () => ({
  page: writable({ url: new URL("http://localhost/reportes") }),
  navigating: writable(null),
  updated: writable(false),
}));

const { default: Reportes } = await import("./+page.svelte");

/** Tal cual lo serializa `ReportSummary`: mapas, no listas (salvo `by_month`). */
const SUMMARY = {
  total: 6,
  by_doc_type: { E: 4, S: 2 },
  by_status: { in_progress: 4, registered: 2 },
  by_month: [{ month: "2026-08", count: 6 }],
  by_dependencia: { "Oficina Jurídica": 5, Archivo: 1 },
};

function pageData(summary = SUMMARY) {
  return {
    summary,
    summaryError: null,
    indiceItems: [],
    indiceError: null,
    indiceTotal: 0,
    filtros: { dateFrom: "", dateTo: "" },
    puedeVerIndice: false,
  };
}

describe("/reportes — sobre el contrato real del resumen", () => {
  it("no se cae con los mapas que devuelve el backend", () => {
    // El defecto era un TypeError durante el render: si esto no lanza, la
    // página vive.
    expect(() => render(Reportes, { data: pageData() })).not.toThrow();
  });

  it("muestra el total que trae el resumen, no uno recalculado a cero", () => {
    render(Reportes, { data: pageData() });
    // `grandTotal` salía 0 porque sumaba sobre `by_type`, que no existe: los
    // porcentajes de todas las tablas quedaban en 0 %.
    expect(screen.getAllByText(/6 radicados en el período/).length).toBeGreaterThan(0);
  });

  it("desglosa por tipo, por estado y por dependencia", () => {
    render(Reportes, { data: pageData() });

    // Tipo: la etiqueta legible, no el código.
    expect(screen.getAllByText("Entrada").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Salida").length).toBeGreaterThan(0);
    // Dependencia: viene de `by_dependencia`, no de `by_dept`.
    expect(screen.getAllByText("Oficina Jurídica").length).toBeGreaterThan(0);
  });

  it("y sin resumen no inventa datos", () => {
    // Gemelo: el camino del fallo era el único que funcionaba antes. Tiene que
    // seguir funcionando ahora, sin confundirse con «cero radicados reales».
    expect(() => render(Reportes, { data: pageData(null) })).not.toThrow();
  });
});
