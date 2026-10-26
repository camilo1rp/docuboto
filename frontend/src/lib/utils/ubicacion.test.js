// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import { describe, it, expect } from "vitest";
import { etiquetaUbicacion } from "./ubicacion.js";

describe("etiquetaUbicacion", () => {
  it("con ruta, la ruta manda: un código suelto no dice dónde está", () => {
    // En un depósito hay muchas «gaveta 3»; lo que ubica es el camino.
    expect(
      etiquetaUbicacion({ codigo: "GAV-03", nombre: "Gaveta 3", ruta: "SED-001 / DEP-001" }),
    ).toBe("SED-001 / DEP-001 · Gaveta 3");
  });

  it("sin ruta, cae a código y nombre", () => {
    expect(etiquetaUbicacion({ codigo: "SED-001", nombre: "Sede principal" })).toBe(
      "SED-001 — Sede principal",
    );
  });

  it("una raíz cuya ruta es su propio código no se repite", () => {
    expect(etiquetaUbicacion({ codigo: "SED-001", nombre: "Sede", ruta: "SED-001" })).toBe(
      "SED-001 — Sede",
    );
  });

  it("no revienta con datos incompletos", () => {
    expect(typeof etiquetaUbicacion({})).toBe("string");
    expect(typeof etiquetaUbicacion(null)).toBe("string");
  });
});
