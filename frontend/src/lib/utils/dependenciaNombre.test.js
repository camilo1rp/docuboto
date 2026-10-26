// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import { describe, it, expect } from "vitest";
import {
  mapaNombres,
  nombreActual,
  nombreSiCambio,
  parseCodigoDependencia,
  opcionesConHuerfanas,
} from "./dependenciaNombre.js";

const nombres = mapaNombres([
  { codigo: 7, nombre: "Gestión Jurídica" },
  { codigo: "9", nombre: "Archivo" },
  { codigo: 0, nombre: "Inválida" },
  { codigo: 3 },
]);

describe("dependenciaNombre (ADR-032)", () => {
  it("el mapa solo admite códigos enteros positivos con nombre", () => {
    expect([...nombres.keys()].sort()).toEqual([7, 9]);
  });
  it("nombreActual prefiere el catálogo y cae a la instantánea", () => {
    expect(nombreActual(7, "Jurídica", nombres)).toBe("Gestión Jurídica");
    expect(nombreActual(55, "Vieja", nombres)).toBe("Vieja");
    expect(nombreActual(null, null, nombres)).toBe("—");
    expect(nombreActual(7, "Jurídica", null)).toBe("Jurídica");
  });
  it("nombreSiCambio solo devuelve el nombre de hoy cuando difiere", () => {
    expect(nombreSiCambio(7, "Jurídica", nombres)).toBe("Gestión Jurídica");
    expect(nombreSiCambio(9, " archivo ", nombres)).toBeNull();
    expect(nombreSiCambio(55, "X", nombres)).toBeNull();
    expect(nombreSiCambio(null, "X", nombres)).toBeNull();
  });
  it.each([
    [7, 7],
    ["12", 12],
    [" 12 ", 12],
    ["Jurídica", null],
    [0, null],
    [1000000, null],
    [1.5, null],
    ["", null],
    [null, null],
    [undefined, null],
    [true, null],
  ])("parseCodigoDependencia(%j) = %j", (entrada, esperado) => {
    expect(parseCodigoDependencia(entrada)).toBe(esperado);
  });
});

describe("opcionesConHuerfanas", () => {
  const activas = [{ value: "7", label: "Jurídica" }];
  const todas = [
    { codigo: 7, nombre: "Jurídica" },
    { codigo: 9, nombre: "Archivo viejo" },
  ];
  it("no añade nada si el valor está entre las activas", () => {
    expect(opcionesConHuerfanas(activas, todas, ["7"])).toEqual(activas);
  });
  it("muestra la inactiva con su nombre y «(inactiva)»", () => {
    expect(opcionesConHuerfanas(activas, todas, ["9"])[0]).toEqual({
      value: "9",
      label: "Archivo viejo (inactiva)",
    });
  });
  it("un código ausente del catálogo se ve, no se oculta", () => {
    expect(opcionesConHuerfanas(activas, todas, ["55"])[0].label).toMatch(/Código 55/);
  });
});
