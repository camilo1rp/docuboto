// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import { describe, it, expect } from "vitest";
import { escapeCsvFormulaInjection, csvCell, buildCsv } from "./csvExport.js";

describe("escapeCsvFormulaInjection — neutraliza fórmulas CSV (espejo de _csv_safe del backend)", () => {
  it.each([
    ["=SUM(A1:A9)", "'=SUM(A1:A9)"],
    ["+1+1", "'+1+1"],
    ["-1+1", "'-1+1"],
    ["@SUM(A1)", "'@SUM(A1)"],
    ["\tmalicioso", "'\tmalicioso"],
    ["\rmalicioso", "'\rmalicioso"],
  ])("antepone apóstrofo a %s", (input, expected) => {
    expect(escapeCsvFormulaInjection(input)).toBe(expected);
  });

  it("deja intacto un texto normal", () => {
    expect(escapeCsvFormulaInjection("Solicitud de traslado")).toBe("Solicitud de traslado");
  });

  it("deja intactos valores no-string (números, null, undefined)", () => {
    expect(escapeCsvFormulaInjection(42)).toBe(42);
    expect(escapeCsvFormulaInjection(null)).toBe(null);
    expect(escapeCsvFormulaInjection(undefined)).toBe(undefined);
  });

  it("cadena vacía no lanza y se devuelve igual", () => {
    expect(escapeCsvFormulaInjection("")).toBe("");
  });
});

describe("csvCell", () => {
  it("normaliza null/undefined a cadena vacía", () => {
    expect(csvCell(null)).toBe("");
    expect(csvCell(undefined)).toBe("");
  });

  it("entrecomilla valores con coma, comillas o salto de línea", () => {
    expect(csvCell("Bogotá, D.C.")).toBe('"Bogotá, D.C."');
    expect(csvCell('dijo "hola"')).toBe('"dijo ""hola"""');
    expect(csvCell("línea1\nlínea2")).toBe('"línea1\nlínea2"');
  });

  it("aplica el escape anti-fórmula ANTES de decidir si entrecomilla", () => {
    // La celda empieza por '=' y además contiene una coma: debe llevar el
    // apóstrofo Y quedar entrecomillada.
    expect(csvCell("=A1,A2")).toBe('"\'=A1,A2"');
  });
});

describe("buildCsv", () => {
  const columns = [
    { key: "radicado", label: "Radicado" },
    { key: "asunto", label: "Asunto" },
    { key: "_acciones", label: "" },
  ];

  it("omite columnas de solo-presentación (key que empieza por _)", () => {
    const csv = buildCsv(columns, [{ radicado: "2024-E-1", asunto: "A" }]);
    expect(csv).not.toContain("_acciones");
    expect(csv).toContain("Radicado,Asunto");
  });

  it("neutraliza una celda de dato maliciosa dentro de una fila real", () => {
    const csv = buildCsv(columns, [{ radicado: "=cmd|'/c calc'!A1", asunto: "x" }]);
    expect(csv).toContain("'=cmd|'/c calc'!A1");
  });

  it("antepone BOM UTF-8 para que Excel detecte el encoding", () => {
    const csv = buildCsv(columns, []);
    expect(csv.charCodeAt(0)).toBe(0xfeff);
  });
});
