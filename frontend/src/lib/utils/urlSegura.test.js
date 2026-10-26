// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import { describe, expect, it } from "vitest";
import { urlHttpSegura } from "./urlSegura.js";

describe("urlHttpSegura", () => {
  it.each([
    "javascript:alert(1)",
    " JaVaScRiPt:alert(1)",
    "data:text/html,x",
    "//evil.example/p",
    "/relativa",
    "ftp://e/p",
    "https://",
    "",
    null,
    42,
  ])("descarta %s", (v) => expect(urlHttpSegura(v)).toBe(""));

  it("conserva http(s) con ancla", () => {
    expect(urlHttpSegura("https://e.example/p#a")).toBe("https://e.example/p#a");
    expect(urlHttpSegura("http://e.example/p")).toBe("http://e.example/p");
  });
});
