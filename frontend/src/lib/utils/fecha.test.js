// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import { describe, it, expect } from "vitest";
import { formatearFecha, formatearFechaHora } from "./fecha.js";

describe("formatearFecha", () => {
  it("una fecha pura NO retrocede un día", () => {
    // El defecto real: un préstamo pactado al 19 se mostraba «18/09/2026»
    // porque `new Date("2026-09-19")` es medianoche UTC y el navegador
    // colombiano (UTC−5) la traduce al día anterior.
    expect(formatearFecha("2026-09-19")).toBe("19/09/2026");
  });

  it("tampoco el primero de enero, que es cuando más se nota", () => {
    // Un plazo de retención que vence el 1 de enero se mostraba «31/12» del
    // año ANTERIOR: mal el día, el mes y el año a la vez.
    expect(formatearFecha("2027-01-01")).toBe("01/01/2027");
  });

  it("un instante con hora sí se convierte a la zona del lector", () => {
    // 12:53 UTC es aún el mismo día en Bogotá: la conversión es correcta y
    // debe seguir ocurriendo. Lo que no puede es aplicarse a un dato que no
    // lleva zona.
    expect(formatearFecha("2026-08-19T12:53:11+00:00")).toBe("19/08/2026");
  });

  it("estilo largo para los documentos que se leen, no se cotejan", () => {
    expect(formatearFecha("2026-09-19", { estilo: "largo" })).toBe("19 de septiembre de 2026");
  });

  it("sin valor no inventa una fecha", () => {
    expect(formatearFecha(null)).toBe("—");
    expect(formatearFecha(undefined)).toBe("—");
    expect(formatearFecha("")).toBe("—");
  });

  it("un valor ilegible se muestra tal cual, no como «Invalid Date»", () => {
    expect(formatearFecha("mañana")).toBe("mañana");
  });
});

describe("formatearFechaHora", () => {
  it("una fecha pura no gana unas 00:00 que además serían del día anterior", () => {
    expect(formatearFechaHora("2026-09-19")).toBe("19/09/2026");
  });

  it("un instante lleva su hora", () => {
    const salida = formatearFechaHora("2026-08-19T12:53:11+00:00");
    expect(salida).toMatch(/19\/08\/2026/);
    expect(salida).toMatch(/\d{1,2}:\d{2}/);
  });

  it("sin valor, un guion", () => {
    expect(formatearFechaHora(null)).toBe("—");
  });
});
